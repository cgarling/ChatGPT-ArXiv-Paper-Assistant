import dataclasses
import json
import math
import re
import time
from openai import APIConnectionError, APIStatusError, OpenAI
from tqdm import tqdm
from typing import Dict, List, Tuple

from arxiv_assistant import environment as env
from arxiv_assistant.utils.pricing import MODEL_PRICING
from arxiv_assistant.utils.utils import EnhancedJSONEncoder, Paper, batched

ABSTRACT_CUTOFF = 4000


class ProviderBudgetExhausted(RuntimeError):
    pass


def calc_price(model, usage):
    if model not in MODEL_PRICING:
        print(f"Model \"{model}\" not found in pricing table, skip pricing calculation")
        return 0, 0

    cached_tokens = usage.model_extra.get("prompt_tokens_details", {}).get("cached_tokens", 0)
    prompt_tokens = usage.prompt_tokens - cached_tokens
    completion_tokens = usage.completion_tokens

    cache_pricing = MODEL_PRICING[model]["cache"] if "cache" in MODEL_PRICING[model] else MODEL_PRICING[model]["prompt"]
    prompt_pricing = MODEL_PRICING[model]["prompt"]
    completion_pricing = MODEL_PRICING[model]["completion"]

    cache_cost = cache_pricing * cached_tokens / 1_000_000
    prompt_cost = prompt_pricing * prompt_tokens / 1_000_000
    completion_cost = completion_pricing * completion_tokens / 1_000_000

    return cache_cost + prompt_cost, completion_cost


def paper_to_titles(paper_entry: Paper) -> str:
    return (
        "ArXiv ID: "
        + paper_entry.arxiv_id
        + "\n"
        + "Title: "
        + paper_entry.title
    )


def paper_to_string(paper_entry: Paper) -> str:
    # renders each paper into a string to be processed by GPT
    return (
        "ArXiv ID: "
        + paper_entry.arxiv_id
        + "\n"
        + "Title: "
        + paper_entry.title
        + "\n"
        + "Authors: "
        + ", ".join(paper_entry.authors)
        + "\n"
        + "Abstract: "
        + paper_entry.abstract[:ABSTRACT_CUTOFF]
    )


def get_user_prompt_for_title_filtering(topic_prompt, postfix_prompt, batch_str):
    user_prompt = "\n\n".join(
        [
            topic_prompt,
            "## Papers",
            "\n\n".join(batch_str),
            postfix_prompt,
        ]
    )
    return user_prompt


def get_user_prompt_for_abstract_filtering(topic_prompt, score_prompt, postfix_prompt, batch_str):
    user_prompt = "\n\n".join(
        [
            topic_prompt,
            score_prompt,
            "## Papers",
            "\n\n".join(batch_str),
            postfix_prompt,
        ]
    )
    return user_prompt


def get_batch_size(batch_size, paper_num, config):
    use_adaptive = config["SELECTION"].getboolean("adaptive_batch_size")
    adaptive_threshold = int(config["SELECTION"]["adaptive_threshold"])

    if use_adaptive and adaptive_threshold > 0:
        if paper_num <= adaptive_threshold:
            scale_factor = 1
        else:
            scale_factor = math.ceil(math.log(paper_num / adaptive_threshold, 2) + 1)
    else:
        scale_factor = 1

    print(f"Base batch size: {batch_size}, scale factor: {scale_factor}")
    return int(batch_size * scale_factor)


PROVIDER_SETTINGS = {
    "google": (env.GOOGLE_API_KEY, env.GOOGLE_OPENAI_BASE_URL),
    "openrouter": (env.OPENROUTER_API_KEY, env.OPENROUTER_OPENAI_BASE_URL),
    "deepseek": (env.DEEPSEEK_API_KEY, env.DEEPSEEK_OPENAI_BASE_URL),
}


def build_providers(config):
    names = [name.strip() for name in config["SELECTION"]["providers"].split(",") if name.strip()]
    if not names or len(names) != len(set(names)):
        raise ValueError("providers must contain unique comma-separated provider names")
    unknown = set(names) - PROVIDER_SETTINGS.keys()
    if unknown:
        raise ValueError(f"Unknown providers: {', '.join(sorted(unknown))}")

    providers = []
    for name in names:
        api_key, base_url = PROVIDER_SETTINGS[name]
        if not api_key:
            raise ValueError(f"Provider '{name}' requires {name.upper()}_API_KEY")
        providers.append({
            "name": name,
            "model": config["SELECTION"][f"{name}_model"],
            "limit_per_minute": int(config["SELECTION"][f"{name}_limit_per_minute"]),
            "max_requests": int(config["SELECTION"][f"{name}_max_requests"]),
            "client": OpenAI(api_key=api_key, base_url=base_url, max_retries=0),
            "last_query_time": None,
            "query_cnt": 0,
            "models_used": [],
        })
    return providers


def call_provider(system_prompt, user_prompt, provider):
    def call():
        kwargs = {
            "model": provider["model"],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.0,
        }
        if provider["name"] == "deepseek":
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        return provider["client"].chat.completions.create(**kwargs)

    for attempt in range(3):
        if provider["max_requests"] > 0 and provider["query_cnt"] >= provider["max_requests"]:
            raise ProviderBudgetExhausted(f"Request budget exhausted ({provider['max_requests']} requests)")

        if provider["limit_per_minute"] > 0 and provider["last_query_time"] is not None:
            interval = 60 / provider["limit_per_minute"]
            time.sleep(max(0, interval - (time.monotonic() - provider["last_query_time"])))

        provider["query_cnt"] += 1
        provider["last_query_time"] = time.monotonic()
        try:
            completion = call()
            actual_model = getattr(completion, "model", None) or provider["model"]
            if actual_model not in provider["models_used"]:
                provider["models_used"].append(actual_model)
            return completion
        except (APIConnectionError, APIStatusError) as ex:
            status_code = getattr(ex, "status_code", None)
            if attempt == 2 or (status_code is not None and status_code != 429 and status_code < 500):
                raise
            delay = 30 * (attempt + 1)
            print(f"Transient {provider['name']} API error ({status_code or 'connection'}); retrying in {delay}s")
            time.sleep(delay)


def switch_provider(providers, provider, reason):
    if len(providers) == 1:
        return False
    if not providers or providers[0] is not provider:
        raise RuntimeError("Cannot switch a provider that is not active")
    providers.pop(0)
    print(f"{provider['name']} {reason}; switching to {providers[0]['name']}")
    return True


def call_model(system_prompt, user_prompt, providers):
    while providers:
        provider = providers[0]
        try:
            return call_provider(system_prompt, user_prompt, provider), provider
        except (APIConnectionError, APIStatusError, ProviderBudgetExhausted) as ex:
            message = model_error_message(ex)
            if not switch_provider(providers, provider, f"failed ({message})"):
                raise RuntimeError(f"{provider['name']} failed: {message}") from ex
    raise RuntimeError("No model providers available")


def call_parsed_model(system_prompt, user_prompt, providers, parse_response):
    attempts = []
    while True:
        completion, provider = call_model(system_prompt, user_prompt, providers)
        attempts.append((completion, provider))
        parsed = parse_response(completion)
        if parsed is not None or not switch_provider(providers, provider, "returned an invalid response"):
            return completion, provider, parsed, attempts


def model_error_message(ex):
    status_code = getattr(ex, "status_code", None)
    body = getattr(ex, "body", None)
    if isinstance(body, list) and body:
        body = body[0]
    if isinstance(body, dict):
        error = body.get("error", body)
        message = error.get("message") if isinstance(error, dict) else None
        if message:
            return f"HTTP {status_code}: {message}" if status_code else message
    return f"HTTP {status_code}: {ex}" if status_code else str(ex)


def parse_title_response(completion, expected_ids):
    try:
        parsed = json.loads(completion.choices[0].message.content)
    except (AttributeError, TypeError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, list) or not all(isinstance(arxiv_id, str) for arxiv_id in parsed):
        return None
    return set(parsed) if len(parsed) == len(set(parsed)) and set(parsed) <= expected_ids else None


def filter_papers_by_title(
    paper_list, providers, system_prompt, topic_prompt, postfix_prompt, config, retry=3,
) -> Tuple[List[Paper], Dict, float, float, int, int]:
    batch_size = get_batch_size(int(config["SELECTION"]["title_batch_size"]), len(paper_list), config)
    print(f"Using batch size of {batch_size} for title filtering")
    batches_of_papers = batched(paper_list, batch_size)

    invalid_paper_list = []  # papers failed to be filtered by GPT, recorded for retrying
    new_paper_list = []
    filtered_results = {}
    total_prompt_cost = 0.0
    total_completion_cost = 0.0
    prompt_tokens = 0
    completion_tokens = 0
    for batch in tqdm(batches_of_papers, desc="Filtering title"):
        # prepare input
        papers_string = [paper_to_titles(paper) for paper in batch]
        user_prompt = get_user_prompt_for_title_filtering(topic_prompt, postfix_prompt, papers_string)
        batch_ids = {paper.arxiv_id for paper in batch}
        try:
            completion, provider, filtered_set, attempts = call_parsed_model(
                system_prompt, user_prompt, providers, lambda result: parse_title_response(result, batch_ids),
            )
        except Exception as ex:
            raise RuntimeError(f"Model request failed for title batch of {len(batch)} papers: {model_error_message(ex)}") from None

        for attempt_completion, attempt_provider in attempts:
            attempt_prompt_cost, attempt_completion_cost = calc_price(attempt_provider["model"], attempt_completion.usage)
            total_prompt_cost += attempt_prompt_cost
            total_completion_cost += attempt_completion_cost
            prompt_tokens += attempt_completion.usage.prompt_tokens
            completion_tokens += attempt_completion.usage.completion_tokens
            print({"provider": attempt_provider["name"], "model": getattr(attempt_completion, "model", None) or attempt_provider["model"], "prompt": {"tokens": attempt_completion.usage.prompt_tokens, "cost": attempt_prompt_cost}, "completion": {"tokens": attempt_completion.usage.completion_tokens, "cost": attempt_completion_cost}})

        if filtered_set is None:
            invalid_paper_list.extend(batch)
            if config["OUTPUT"].getboolean("debug_messages"):
                print("Failed to parse a complete title-filter response")
                print(f"`out_text`: {completion.choices[0].message.content}")
            continue

        for paper in batch:
            if paper.arxiv_id in filtered_set:
                filtered_results[paper.arxiv_id] = {
                    "COMMENT": "Title filtered",
                    "SCORE": 0,
                    **dataclasses.asdict(paper),
                }
                print(f"Filtered out paper {paper.arxiv_id} by title ({paper.title})")
            else:
                new_paper_list.append(paper)

    print(f"Filtered {len(filtered_results)} papers based on title with cost of ${total_prompt_cost + total_completion_cost}, remaining {len(new_paper_list)} papers:\n"
          f"({prompt_tokens} prompt tokens cost ${total_prompt_cost})\n"
          f"({completion_tokens} completion tokens cost ${total_completion_cost})")

    if len(invalid_paper_list) > 0:
        if retry > 0:
            print(f"Retrying {len(invalid_paper_list)} papers failed to be filtered by GPT through title filtering (left {retry - 1} retries)")
            retried_new_paper_list, retried_filtered_results, retried_total_prompt_cost, retried_total_completion_cost, retried_prompt_tokens, retried_completion_tokens = filter_papers_by_title(
                invalid_paper_list,
                providers,
                system_prompt,
                topic_prompt,
                postfix_prompt,
                config,
                retry - 1,
            )
            new_paper_list.extend(retried_new_paper_list)
            filtered_results.update(retried_filtered_results)
            total_prompt_cost += retried_total_prompt_cost
            total_completion_cost += retried_total_completion_cost
            prompt_tokens += retried_prompt_tokens
            completion_tokens += retried_completion_tokens
        else:
            print(f"Maximum retries reached, skip retrying")
            print(f"Left {len(invalid_paper_list)} papers failed to be filtered by GPT through title filtering")
            print(f"Invalid paper titles:")
            for paper in invalid_paper_list:
                print(f"{paper.title}")

    return new_paper_list, filtered_results, total_prompt_cost, total_completion_cost, prompt_tokens, completion_tokens


def parse_chatgpt(raw_out_text, config):
    # just runs the chatgpt prompt, tries to parse the resulting JSON
    out_text = re.sub("```jsonl\n", "", raw_out_text)
    out_text = re.sub("```", "", out_text)
    out_text = re.sub(r"\n+", "\n", out_text)
    out_text = re.sub("},", "}", out_text).strip()

    # split out_text line by line and parse each as a json.
    json_dicts = []
    invalid_cnt = 0  # the number of papers that cannot be identified according to the model output

    for line in out_text.split("\n"):
        # try catch block to attempt to parse json
        try:
            json_dicts.append(json.loads(line))
        except Exception as ex:
            invalid_cnt += 1
            if config["OUTPUT"].getboolean("debug_messages"):
                print(f"Exception happened: Failed to parse LM output as json ({ex})")
                print(f"RAW output: {raw_out_text}")
                print(f"`out_text`: {out_text}")
            continue
    return json_dicts, invalid_cnt


def parse_abstract_response(completion, expected_ids, config):
    try:
        json_dicts, invalid_cnt = parse_chatgpt(completion.choices[0].message.content, config)
        if invalid_cnt or len(json_dicts) != len(expected_ids):
            return None
        returned_ids = []
        for result in json_dicts:
            returned_ids.append(result["ARXIVID"])
            if not isinstance(result["COMMENT"], str):
                return None
            if not 1 <= int(result["RELEVANCE"]) <= 10 or not 1 <= int(result["NOVELTY"]) <= 10:
                return None
        return json_dicts if len(returned_ids) == len(set(returned_ids)) and set(returned_ids) == expected_ids else None
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def filter_papers_by_abstract(
    paper_list, id_paper_mapping, providers, system_prompt, topic_prompt, score_prompt, postfix_prompt, config, retry=3,
) -> Tuple[List[List[Dict]], Dict, Dict, float, float, int, int]:
    batch_size = get_batch_size(int(config["SELECTION"]["abstract_batch_size"]), len(paper_list), config)
    print(f"Using batch size of {batch_size} for abstract filtering")
    batches_of_papers = batched(paper_list, batch_size)

    invalid_arxiv_ids = set()  # arxiv ids of papers failed to be scored by GPT, recorded for retrying
    scored_batches = []
    selected_results = {}
    filtered_results = {}
    total_prompt_cost = 0.0
    total_completion_cost = 0.0
    prompt_tokens = 0
    completion_tokens = 0
    for batch in tqdm(batches_of_papers, desc="Filtering abstract"):
        # temp values
        this_scored_batch = []
        all_arxiv_ids = {paper.arxiv_id for paper in batch}
        finished_arxiv_ids = set()

        # prepare input
        batch_str = [paper_to_string(paper) for paper in batch]
        user_prompt = get_user_prompt_for_abstract_filtering(topic_prompt, score_prompt, postfix_prompt, batch_str)
        try:
            completion, provider, json_dicts, attempts = call_parsed_model(
                system_prompt, user_prompt, providers, lambda result: parse_abstract_response(result, all_arxiv_ids, config),
            )
        except Exception as ex:
            raise RuntimeError(f"Model request failed for abstract batch of {len(batch)} papers after retries: {model_error_message(ex)}") from None

        for attempt_completion, attempt_provider in attempts:
            attempt_prompt_cost, attempt_completion_cost = calc_price(attempt_provider["model"], attempt_completion.usage)
            total_prompt_cost += attempt_prompt_cost
            total_completion_cost += attempt_completion_cost
            prompt_tokens += attempt_completion.usage.prompt_tokens
            completion_tokens += attempt_completion.usage.completion_tokens
            print({"provider": attempt_provider["name"], "model": getattr(attempt_completion, "model", None) or attempt_provider["model"], "prompt": {"tokens": attempt_completion.usage.prompt_tokens, "cost": attempt_prompt_cost}, "completion": {"tokens": attempt_completion.usage.completion_tokens, "cost": attempt_completion_cost}})

        if json_dicts is None:
            invalid_arxiv_ids.update(all_arxiv_ids)
            scored_batches.append([])
            if config["OUTPUT"].getboolean("debug_messages"):
                print("Failed to parse a complete abstract-filter response")
                print(f"`out_text`: {completion.choices[0].message.content}")
            continue

        for jdict in json_dicts:
            if int(jdict["RELEVANCE"]) < 7:
                jdict["COMMENT"] = ""
            if jdict["ARXIVID"] not in id_paper_mapping:
                if config["OUTPUT"].getboolean("debug_messages"):
                    print(f"Exception happened: ARXIVID \"{jdict['ARXIVID']}\" not found in `id_paper_mapping`")
                continue

            result = {
                "SCORE": 2 * int(jdict["RELEVANCE"]) + int(jdict["NOVELTY"]),
                **jdict,
                **dataclasses.asdict(id_paper_mapping[jdict["ARXIVID"]]),
            }
            this_scored_batch.append(result)

            filtered = (
                int(jdict["RELEVANCE"]) < int(config["FILTERING"]["relevance_cutoff"]) or
                int(jdict["NOVELTY"]) < int(config["FILTERING"]["novelty_cutoff"])
            )
            if filtered:
                filtered_results[jdict["ARXIVID"]] = result
                print(f"Filtered out paper {jdict['ARXIVID']} by score (RELEVANCE={jdict['RELEVANCE']}, NOVELTY={jdict['NOVELTY']}) ({id_paper_mapping[jdict['ARXIVID']].title})")
            else:
                selected_results[jdict["ARXIVID"]] = result

            finished_arxiv_ids.add(jdict["ARXIVID"])
        scored_batches.append(this_scored_batch)

        # check if all papers are finished
        this_invalid_arxiv_ids = all_arxiv_ids - finished_arxiv_ids
        if len(this_invalid_arxiv_ids) > 0:
            invalid_arxiv_ids.update(this_invalid_arxiv_ids)

    print(f"Filtered {len(filtered_results)} papers based on abstract with cost of ${total_prompt_cost + total_completion_cost}, remaining {len(selected_results)} papers:\n"
          f"({prompt_tokens} prompt tokens cost ${total_prompt_cost})\n"
          f"({completion_tokens} completion tokens cost ${total_completion_cost})")

    # retry invalid arxiv ids
    if len(invalid_arxiv_ids) > 0:
        if retry > 0:
            print(f"Retrying {len(invalid_arxiv_ids)} papers failed to be scored by GPT through abstract filtering (left {retry - 1} retries)")
            retried_scored_batches, retried_selected_results, retried_filtered_results, retried_total_prompt_cost, retried_total_completion_cost, retried_prompt_tokens, retried_completion_tokens = filter_papers_by_abstract(
                [id_paper_mapping[arxiv_id] for arxiv_id in invalid_arxiv_ids],
                id_paper_mapping,
                providers,
                system_prompt,
                topic_prompt,
                score_prompt,
                postfix_prompt,
                config,
                retry - 1,
            )
            scored_batches.extend(retried_scored_batches)
            selected_results.update(retried_selected_results)
            filtered_results.update(retried_filtered_results)
            total_prompt_cost += retried_total_prompt_cost
            total_completion_cost += retried_total_completion_cost
            prompt_tokens += retried_prompt_tokens
            completion_tokens += retried_completion_tokens
        else:
            print(f"Maximum retries reached, skip retrying")
            print(f"Left {len(invalid_arxiv_ids)} papers failed to be scored by GPT through abstract filtering")
            print(f"Invalid paper titles:")
            for arxiv_id in invalid_arxiv_ids:
                print(f"{id_paper_mapping[arxiv_id].title}")

    return scored_batches, selected_results, filtered_results, total_prompt_cost, total_completion_cost, prompt_tokens, completion_tokens


def filter_by_gpt(paper_list, system_prompt, topic_prompt, score_prompt, postfix_prompt_title, postfix_prompt_abstract, config):
    providers = build_providers(config)
    all_providers = providers.copy()
    print(f"Model providers: {', '.join(provider['name'] for provider in providers)}")
    total_filtered_results = {}
    total_prompt_cost = 0.0
    total_completion_cost = 0.0
    total_prompt_tokens = 0
    total_completion_tokens = 0

    id_paper_mapping: Dict[str, Paper] = {paper.arxiv_id: paper for paper in paper_list}

    # filter papers by titles
    if config["SELECTION"].getboolean("run_title_filter"):
        paper_list, filtered_results, prompt_cost, completion_cost, prompt_tokens, completion_tokens = filter_papers_by_title(
            paper_list,
            providers,
            system_prompt,
            topic_prompt,
            postfix_prompt_title,
            config,
            retry=int(config["SELECTION"]["title_retry"]),
        )
    else:
        filtered_results = {}
        prompt_cost, completion_cost, prompt_tokens, completion_tokens = 0.0, 0.0, 0, 0
        print("Skipping GPT title filtering")

    total_filtered_results.update(filtered_results)
    total_prompt_cost += prompt_cost
    total_completion_cost += completion_cost
    total_prompt_tokens += prompt_tokens
    total_completion_tokens += completion_tokens

    # filter remaining papers by abstracts
    if config["SELECTION"].getboolean("run_abstract_filter"):
        scored_batches, selected_results, filtered_results, prompt_cost, completion_cost, prompt_tokens, completion_tokens = filter_papers_by_abstract(
            paper_list,
            id_paper_mapping,
            providers,
            system_prompt,
            topic_prompt,
            score_prompt,
            postfix_prompt_abstract,
            config,
            retry=int(config["SELECTION"]["abstract_retry"]),
        )
    else:
        scored_batches = []
        selected_results = {paper.arxiv_id: {**dataclasses.asdict(paper)} for paper in paper_list}
        filtered_results = {}
        prompt_cost, completion_cost, prompt_tokens, completion_tokens = 0.0, 0.0, 0, 0
        print("Skipping GPT abstract filtering")

    total_filtered_results.update(filtered_results)
    total_prompt_cost += prompt_cost
    total_completion_cost += completion_cost
    total_prompt_tokens += prompt_tokens
    total_completion_tokens += completion_tokens

    if config["OUTPUT"].getboolean("dump_debug_file"):
        with open(env.OUTPUT_DEBUG_FILE_FORMAT.format("gpt_paper_batches.json"), "w") as outfile:
            json.dump(scored_batches, outfile, cls=EnhancedJSONEncoder, indent=4)

    print(f"Total cost is ${total_prompt_cost + total_completion_cost}:\n"
          f"({total_prompt_tokens} prompt tokens cost ${total_prompt_cost})\n"
          f"({total_completion_tokens} completion tokens cost ${total_completion_cost})")

    used_models = [f"{provider['name']} ({model})" for provider in all_providers for model in provider["models_used"]]
    return selected_results, total_filtered_results, total_prompt_cost, total_completion_cost, total_prompt_tokens, total_completion_tokens, used_models

# if __name__ == "__main__":
#     openai_client = OpenAI(api_key=env.GOOGLE_API_KEY, base_url=env.GOOGLE_OPENAI_BASE_URL)
#
#     # loads papers from 'in/debug_papers.json' and filters them
#     with open("../../in/debug_papers.json", "r") as f:
#         paper_list_in_dict = json.load(f)
#
#     papers = [
#         [
#             Paper(
#                 arxiv_id=paper["arxiv_id"],
#                 authors=paper["authors"],
#                 title=paper["title"],
#                 abstract=paper["abstract"],
#             )
#             for paper in batch
#         ]
#         for batch in paper_list_in_dict
#     ]
#     all_papers = {}
#     paper_outputs = {}
#     sort_dict = {}
#     total_cost = 0
#     for batch in tqdm(papers):
#         batch_str = [paper_to_string(paper) for paper in batch]
#         user_prompt = get_full_prompt_for_abstract_filtering(SYSTEM_PROMPT, TOPIC_PROMPT, SCORE_PROMPT, POSTFIX_PROMPT_ABSTRACT, batch_str)
#         model = CONFIG["SELECTION"]["model"]
#         completion = call_chatgpt(user_prompt, openai_client, model)
#
#         prompt_cost, completion_cost = calc_price(model, completion.usage)
#         total_cost += prompt_cost + completion_cost
#         out_text = completion.choices[0].message.content
#
#         json_dicts = parse_chatgpt(out_text, CONFIG)
#         for paper in batch:
#             all_papers[paper.arxiv_id] = paper
#         for jdict in json_dicts:
#             paper_outputs[jdict["ARXIVID"]] = {
#                 **dataclasses.asdict(all_papers[jdict["ARXIVID"]]),
#                 **jdict,
#             }
#             sort_dict[jdict["ARXIVID"]] = jdict["RELEVANCE"] + jdict["NOVELTY"]
#
#     # sort the papers by relevance and novelty
#     print("total cost:" + str(total_cost))
#     keys = list(sort_dict.keys())
#     values = list(sort_dict.values())
#
#
#     def argsort(seq):
#         return sorted(range(len(seq)), key=seq.__getitem__)
#
#
#     sorted_keys = [keys[idx] for idx in argsort(values)[::-1]]
#     selected_papers = {key: paper_outputs[key] for key in sorted_keys}
#
#     with open(OUTPUT_DEBUG_FILE_FORMAT.format("filter_paper_test.json"), "w") as outfile:
#         json.dump(selected_papers, outfile, cls=EnhancedJSONEncoder, indent=4)
