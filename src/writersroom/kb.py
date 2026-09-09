"""知识库检索：frontmatter 元数据 + 自研 BM25Okapi 排序（docs/design/spec.md §4.8）。

- 索引 knowledge/*.md：python-frontmatter 解析元数据，title/tags 加权 ×2（META_BOOST），
  标题行与正文一起作为文档正文；
- 排序为纯标准库自研 BM25Okapi（k1=1.5, b=0.75；公式以 rank_bm25.BM25Okapi 为对照参考，
  idf 取其非负形式 ln(1 + (N - df + 0.5) / (df + 0.5))），见 _bm25_scores docstring；
- 分词用 core.tokenize；同输入同参数 → 同分同序（命中按 (-score, file) 排序）；
- 无命中 → hits=[] 且带 message="未命中"，并给出已索引文件数（CLI 层负责打印）。
"""
from __future__ import annotations

import math
from collections import Counter
from pathlib import Path

import frontmatter

from .core import (
    TOOL, WritersRoomError, Workspace, now_iso, sha256_file, sha256_text, tokenize,
)

K1 = 1.5
B = 0.75
META_BOOST = 2   # frontmatter title/tags 的词频权重倍数
MAX_CONTEXT = 3  # 每个命中文件最多保留的摘录条数


def _bm25_scores(query_tokens: list[str], docs_tokens: list[list[str]]) -> list[float]:
    """BM25Okapi 打分（自研，纯标准库；对照 rank_bm25.BM25Okapi 公式实现）。

    score(q, d) = Σ_t idf(t) · f(t,d)·(k1+1) / (f(t,d) + k1·(1 − b + b·|d|/avgdl))
    idf(t) = ln(1 + (N − df(t) + 0.5) / (df(t) + 0.5))   # 非负形式
    """
    n_docs = len(docs_tokens)
    if n_docs == 0:
        return []
    avgdl = sum(len(d) for d in docs_tokens) / n_docs
    tfs = [Counter(d) for d in docs_tokens]
    df: Counter[str] = Counter()
    for tf in tfs:
        for t in tf:
            df[t] += 1
    scores = []
    for tf in tfs:
        dl = sum(tf.values())
        denom_norm = K1 * (1.0 - B + B * dl / avgdl) if avgdl > 0 else K1
        score = 0.0
        for t in set(query_tokens):
            f = tf.get(t, 0)
            if f == 0:
                continue
            idf = math.log(1.0 + (n_docs - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * f * (K1 + 1.0) / (f + denom_norm)
        scores.append(score)
    return scores


def _doc_tokens(title: str, tags: list[str], body: str) -> tuple[list[str], str]:
    """title/tags 加权 ×2 计入词频；返回 (tokens, 实际分词方法)。"""
    meta_tokens, m1 = tokenize(" ".join([title] + tags))
    body_tokens, m2 = tokenize(body)
    return meta_tokens * META_BOOST + body_tokens, (m2 if body.strip() else m1)


def _contexts(body: str, query_set: set[str]) -> list[dict]:
    """命中行 ±1 行摘录（最多 MAX_CONTEXT 条）；line 为 1 起始行号。"""
    lines = body.splitlines()
    out: list[dict] = []
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        line_tokens, _ = tokenize(line)
        if query_set & set(line_tokens):
            start, end = max(0, i - 1), min(len(lines), i + 2)
            excerpt = "\n".join(ln for ln in lines[start:end]).strip()
            out.append({"line": i + 1, "excerpt": excerpt})
            if len(out) >= MAX_CONTEXT:
                break
    return out


def search(dir_or_ws, terms: list[str], top: int = 10) -> dict:
    """检索知识库，返回 {terms, indexed_files, hits, ...}；无命中带 message="未命中"。"""
    kb_dir = dir_or_ws.path("knowledge") if isinstance(dir_or_ws, Workspace) else Path(dir_or_ws)
    if not kb_dir.is_dir():
        raise WritersRoomError(
            f"知识库目录不存在：{kb_dir}",
            "先在工作区 knowledge/ 放入 md 笔记，或用 --dir 指定已有目录",
        )
    terms = [t.strip() for t in terms if t and t.strip()]
    if not terms:
        raise WritersRoomError("未提供检索词", "用法：writersroom kb search <词…>")

    files = sorted(kb_dir.glob("*.md"), key=lambda p: p.name)
    docs: list[tuple[str, list[str], str]] = []   # (文件名, 加权 tokens, 正文)
    tokenizer_used = "char_ngram"
    for p in files:
        post = frontmatter.load(p)
        title = str(post.metadata.get("title") or "")
        tags = post.metadata.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        tokens, used = _doc_tokens(title, [str(t) for t in tags], post.content)
        tokenizer_used = used
        docs.append((p.name, tokens, post.content))

    query_tokens: list[str] = []
    term_token_sets: dict[str, set[str]] = {}
    for t in terms:
        toks, _ = tokenize(t)
        term_token_sets[t] = set(toks)
        query_tokens.extend(toks)
    query_set = set(query_tokens)

    scores = _bm25_scores(query_tokens, [d[1] for d in docs])
    hits: list[dict] = []
    for (name, tokens, body), score in zip(docs, scores):
        if score <= 0:
            continue
        doc_set = set(tokens)
        matched = sorted({tok for t in terms for tok in (term_token_sets[t] & doc_set)})
        if not matched:
            continue
        hits.append({
            "file": name,
            "score": round(score, 6),
            "matched_terms": matched,
            "context": _contexts(body, query_set),
        })
    hits.sort(key=lambda h: (-h["score"], h["file"]))

    result = {
        "terms": terms,
        "tokenizer": tokenizer_used,
        "indexed_files": len(files),
        "hits": hits[:max(top, 0)],
        "produced_by": TOOL,
        "created_at": now_iso(),
        "source_hash": sha256_text("\n".join(f"{p.name}:{sha256_file(p)}" for p in files)),
    }
    if not result["hits"]:
        result["message"] = "未命中"
    return result
