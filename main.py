import argparse
import json
import re
import shutil
from datetime import date, timedelta
from pathlib import Path


def parse_iso_date(value):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise argparse.ArgumentTypeError(f"invalid ISO date: {value!r} (expected YYYY-MM-DD)")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"invalid ISO date: {value!r}") from error


def infer_search_range(output_date):
    weekday = output_date.weekday()
    if weekday == 0:  # Sunday announcement: Thursday 14:00–Friday 14:00 ET
        return output_date - timedelta(days=4), output_date - timedelta(days=3)
    if weekday == 1:  # Monday announcement: Friday 14:00–Monday 14:00 ET
        return output_date - timedelta(days=4), output_date - timedelta(days=1)
    return output_date - timedelta(days=2), output_date - timedelta(days=1)


def resolve_dates(output_date, search_start, search_end, today=None):
    today = today or date.today()
    if output_date is None:
        if search_start is not None or search_end is not None:
            raise ValueError("--search-start and --search-end require --date")
        return None, None
    if any(value > today for value in (output_date, search_start, search_end) if value is not None):
        raise ValueError("future dates are not allowed")
    if (search_start is None) != (search_end is None):
        raise ValueError("--search-start and --search-end must be supplied together")
    if search_start is None:
        if output_date.weekday() >= 5:
            raise ValueError("weekend output dates require an explicit search range")
        return infer_search_range(output_date)
    if search_start > search_end:
        raise ValueError("--search-start cannot be after --search-end")
    return search_start, search_end


def publish_latest(dated_markdown, output_path, update_latest):
    if update_latest:
        shutil.copy2(dated_markdown, Path(output_path) / "output.md")


def run_pipeline(output_date=None, search_start=None, search_end=None, update_latest=True):
    from arxiv_assistant import environment as env

    if output_date is None:
        output_date = date(env.NOW_YEAR, env.NOW_MONTH, env.NOW_DAY)
        source = "rss"
    else:
        source = "api"
        env.configure_output_date((output_date.year, output_date.month, output_date.day))

    from arxiv_assistant.apis.arxiv import get_papers_from_arxiv
    from arxiv_assistant.apis.semantic_scholar import get_authors
    from arxiv_assistant.filters.filter_author import filter_papers_by_hindex, select_by_author
    from arxiv_assistant.filters.filter_gpt import filter_by_gpt
    from arxiv_assistant.push_to_slack import push_to_slack
    from arxiv_assistant.renderers.render_daily import render_daily_md
    from arxiv_assistant.utils.utils import EnhancedJSONEncoder

    date_tuple = (output_date.year, output_date.month, output_date.day)
    start_tuple = None if search_start is None else (search_start.year, search_start.month, search_start.day)
    end_tuple = None if search_end is None else (search_end.year, search_end.month, search_end.day)
    all_entries, arxiv_paper_dict = get_papers_from_arxiv(env.CONFIG, source=source, begin_date=start_tuple, end_date=end_tuple)
    paper_list = list({paper for area_papers in arxiv_paper_dict.values() for paper in area_papers})
    print(f"Total number of papers: {len(paper_list)}")
    if not paper_list:
        print("No papers found")
        return 0

    if env.CONFIG["SELECTION"].getboolean("run_author_match"):
        author_names = {author for paper in paper_list for author in paper.authors}
        print(f"Getting author info for {len(author_names)} authors")
        all_authors = get_authors(list(author_names), env.S2_API_KEY, config=env.CONFIG)
    else:
        print("Skipping author info")
        all_authors = {}

    if env.CONFIG["OUTPUT"].getboolean("dump_debug_file"):
        debug_values = {
            "config.json": {section: dict(env.CONFIG[section]) for section in env.CONFIG.sections()},
            "author_id_set.json": list(env.AUTHOR_ID_SET),
            "all_papers.json": paper_list,
            "all_authors.json": all_authors,
        }
        for filename, value in debug_values.items():
            with open(env.OUTPUT_DEBUG_FILE_FORMAT.format(filename), "w") as outfile:
                json.dump(value, outfile, cls=EnhancedJSONEncoder, indent=4)

    selected_paper_dict = {}
    filtered_paper_dict = {}
    if env.CONFIG["SELECTION"].getboolean("run_author_match"):
        paper_list, selected_results = select_by_author(all_authors, paper_list, env.AUTHOR_ID_SET, env.CONFIG)
        selected_paper_dict.update(selected_results)
        paper_list, filtered_results = filter_papers_by_hindex(all_authors, paper_list, env.CONFIG)
        filtered_paper_dict.update(filtered_results)
    else:
        print("Skipping selection by author")
        print("Skipping h-index filtering")

    if env.CONFIG["SELECTION"].getboolean("run_openai"):
        selected_results, filtered_results, total_prompt_cost, total_completion_cost, total_prompt_tokens, total_completion_tokens, used_models = filter_by_gpt(
            paper_list, env.SYSTEM_PROMPT, env.TOPIC_PROMPT, env.SCORE_PROMPT, env.POSTFIX_PROMPT_TITLE,
            env.POSTFIX_PROMPT_ABSTRACT, env.CONFIG,
        )
        selected_paper_dict.update(selected_results)
        filtered_paper_dict.update(filtered_results)
    else:
        total_prompt_cost, total_completion_cost, total_prompt_tokens, total_completion_tokens = 0.0, 0.0, 0, 0
        used_models = []
        print("Skipping GPT filtering")

    selected_paper_dict = dict(sorted(
        selected_paper_dict.items(),
        key=lambda item: (item[1].get("SCORE", 0), item[1].get("RELEVANCE", 0)),
        reverse=True,
    ))
    if env.CONFIG["OUTPUT"].getboolean("dump_debug_file"):
        for filename, value in (("selected_paper_dict.json", selected_paper_dict), ("filtered_paper_dict.json", filtered_paper_dict)):
            with open(env.OUTPUT_DEBUG_FILE_FORMAT.format(filename), "w") as outfile:
                json.dump(value, outfile, cls=EnhancedJSONEncoder, indent=4)

    if env.CONFIG["OUTPUT"].getboolean("dump_json"):
        with open(env.OUTPUT_JSON_FILE_FORMAT.format("output.json"), "w") as outfile:
            json.dump(selected_paper_dict, outfile, indent=4)

    markdown_path = env.OUTPUT_MD_FILE_FORMAT.format("output.md")
    if env.CONFIG["OUTPUT"].getboolean("dump_md"):
        head_table = {
            "headers": [f"*[{' → '.join(used_models) or 'No model'}]*", "Prompt", "Completion", "Total"],
            "data": [
                ["**Token**", total_prompt_tokens, total_completion_tokens, total_prompt_tokens + total_completion_tokens],
                ["**Cost**", f"${round(total_prompt_cost, 2)}", f"${round(total_completion_cost, 2)}", f"${round(total_prompt_cost + total_completion_cost, 2)}"],
            ],
        }
        markdown = render_daily_md(
            all_entries, arxiv_paper_dict, selected_paper_dict, now_date=date_tuple,
            prompts=(env.SYSTEM_PROMPT, env.POSTFIX_PROMPT_ABSTRACT, env.SCORE_PROMPT, env.TOPIC_PROMPT), head_table=head_table,
        )
        if source == "api":
            markdown = (
                f"> **Approximate recovery:** arXiv API submission dates {search_start.isoformat()} through {search_end.isoformat()} were used; "
                "these may differ from announcement dates.\n\n" + markdown
            )
        with open(markdown_path, "w") as outfile:
            outfile.write(markdown)
        publish_latest(markdown_path, env.CONFIG["OUTPUT"]["output_path"], update_latest)

    if env.CONFIG["OUTPUT"].getboolean("push_to_slack"):
        if env.SLACK_KEY is None:
            print("Warning: push_to_slack is true, but SLACK_KEY is not set - not pushing to slack")
        else:
            push_to_slack(selected_paper_dict)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Generate the daily arXiv report or recover a historical date.")
    parser.add_argument("--date", type=parse_iso_date, help="historical output date (YYYY-MM-DD)")
    parser.add_argument("--search-start", type=parse_iso_date, help="first arXiv submission date to search")
    parser.add_argument("--search-end", type=parse_iso_date, help="last arXiv submission date to search")
    parser.add_argument("--update-latest", action="store_true", help="also replace out/output.md during recovery")
    args = parser.parse_args(argv)
    try:
        search_start, search_end = resolve_dates(args.date, args.search_start, args.search_end)
    except ValueError as error:
        parser.error(str(error))
    return run_pipeline(args.date, search_start, search_end, update_latest=args.date is None or args.update_latest)


if __name__ == "__main__":
    raise SystemExit(main())
