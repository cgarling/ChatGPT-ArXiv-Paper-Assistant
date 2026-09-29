import assert from "node:assert/strict";
import { filterPapers, mergeReports, renderMath, sortPapers, visibleAffiliationCount, weightedScore } from "../site/app.js";

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
const references = [[1], [2], [2], [], [3], [4]];
assert.equal(visibleAffiliationCount(references, 3), 2);
assert.equal(visibleAffiliationCount(references, references.length), 4);

const rendered = [];
globalThis.renderMathInElement = (node, options) => {
  rendered.push({ text: node.textContent, options });
  node.childNodes = [{ nodeType: 3, textContent: node.textContent }];
};
const math = { textContent: String.raw`Cost \$5. $$x+y=z$$ \(a=b\) \[c=d\] $M_* \sim 1.6\times10^7~M_{\odot}$ $\mathrm{x}\;\text{x}\;\mathbf{x}\;\mathcal{L}\;{\rm H}\;\mbox{km}\;\operatorname{SFR}\;\hat{x}\lesssim y$ $\badcommand{$`, childNodes: [] };
renderMath(math);
renderMath(math);
assert.equal(rendered.length, 1);
assert.match(rendered[0].text, /Cost \\textdollar\{\}5/);
assert.equal(math.childNodes[0].textContent.startsWith("Cost $5."), true);
assert.deepEqual(rendered[0].options.delimiters, [
  { left: "$$", right: "$$", display: true },
  { left: "$", right: "$", display: false },
  { left: "\\(", right: "\\)", display: false },
  { left: "\\[", right: "\\]", display: true },
]);
assert.equal(rendered[0].options.throwOnError, false);
assert.equal(rendered[0].options.errorColor, "inherit");
assert.equal(rendered[0].options.trust, false);
const literalDollars = { textContent: "The instruments cost $5 and USD 10.", childNodes: [] };
renderMath(literalDollars);
assert.equal(rendered.at(-1).text, literalDollars.textContent);
const plainText = { textContent: "No mathematics here.", childNodes: [] };
renderMath(plainText);
assert.equal(rendered.length, 2);
delete globalThis.renderMathInElement;
const fallback = { textContent: "$x$ remains readable", childNodes: [] };
renderMath(fallback);
assert.equal(fallback.textContent, "$x$ remains readable");
console.log("site data functions: ok");
