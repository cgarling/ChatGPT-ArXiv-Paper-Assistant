import assert from "node:assert/strict";
import { filterPapers, mergeReports, sortPapers, weightedScore } from "../site/app.js";

const old = { title: "Old", authors: ["Ada"], abstract: "stars", RELEVANCE: 5, NOVELTY: 9, SCORE: 14 };
const newest = { title: "New", authors: ["Grace"], affiliations: ["Example Collaboration"], abstract: "galaxies", COMMENT: "useful", RELEVANCE: 6, NOVELTY: 4, SCORE: 10 };
const tiedLow = { ...newest, title: "Low", SCORE: 9 };
const tiedHigh = { ...newest, title: "High", SCORE: 12 };
const merged = mergeReports([
  { date: "2025-01-01", data: { "2501.1": old } },
  { date: "2025-01-02", data: { "2501.1": newest, "2501.2": tiedLow } },
  { date: "2025-01-02", data: { "2501.2": tiedHigh } },
]);
assert.equal(merged.find(paper => paper.arxiv_id === "2501.1").title, "New");
assert.equal(merged.find(paper => paper.arxiv_id === "2501.2").title, "High");
assert.equal(weightedScore(old), 19);
assert.equal(sortPapers([newest, old])[0].title, "Old");
assert.deepEqual(filterPapers(merged, "grace", 6, 4).map(paper => paper.arxiv_id).sort(), ["2501.1", "2501.2"]);
console.log("site data functions: ok");
