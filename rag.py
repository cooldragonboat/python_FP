"""MoodTrack 說明小幫手：RAG（檢索增強生成）。

流程：
1. 檢索（Retrieval）：把 docs/help/*.md 依「## 小標題」切成段落，
   用 BM25 演算法找出和問題最相關的幾段。中文沒有空格，所以用「相鄰兩個字」
   （bigram）當作詞，英文和數字則用整個單字。
2. 增強（Augmented）：把找到的段落放進提示詞，要求模型只根據這些內容回答。
3. 生成（Generation）：呼叫本機 Ollama 的模型（預設 gemma4）產生回答。

連不到 Ollama 時（例如部署在雲端主機上），自動退回「只檢索」模式，
直接顯示最相關的說明段落，功能不會中斷。

知識庫只有說明文件，不包含任何使用者的心情紀錄。
"""

from __future__ import annotations

import json
import math
import os
import re
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

HELP_DIR = Path(__file__).resolve().parent / "docs" / "help"

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4")
# 本機模型第一次載入可能要比較久
OLLAMA_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT", "120"))
# 設成 0 可以完全關閉 AI 生成，只用檢索
RAG_USE_LLM = os.environ.get("RAG_USE_LLM", "1") != "0"

TOP_K = 3
# BM25 分數低於這個值，視為「說明文件裡找不到」（例如「今天天氣如何」只碰到「今天」約 3 分）
MIN_SCORE = 3.5
# 第二名以後的段落，分數要達到第一名的這個比例才保留，避免塞進不相關的內容
RELATIVE_CUTOFF = 0.5
# 問題裡的詞，至少要有這個比例出現在段落中才算相關
# （避免「可以換成深色模式嗎」只因為「模式」兩個字就配到不相關的段落）
MIN_COVERAGE = 0.3
# 檢索前先拿掉這些疑問詞（每個問題都會出現，不代表內容），
# 也避免切出「曆怎」這種跨過疑問詞的無意義詞
QUESTION_WORDS = re.compile(r"為什麼|怎麼辦|怎麼|什麼|如何|可以|請問|一下|我要|我想|我的|嗎|呢|？|\?")
CRISIS_HEADING = "需要立即協助時"

CRISIS_KEYWORDS = (
    "自殺", "想死", "不想活", "輕生", "結束生命", "傷害自己", "自殘", "割腕",
    "活不下去", "撐不下去", "去死",
)

SYSTEM_PROMPT = """你是 MoodTrack 網站的說明小幫手。MoodTrack 是一個讓使用者記錄每日心情、壓力、睡眠與運動的網站。

回答規則：
1. 只根據下方「參考資料」回答網站的使用方式；參考資料沒有提到的事情，就說「說明文件裡沒有這個資訊」，並建議使用者到「說明與回報」頁面送出回報。不要自己編造功能或操作步驟。
2. 使用繁體中文，語氣友善，簡潔回答，通常 3～6 句；需要步驟時用 1. 2. 3. 條列。
3. 不提供醫療診斷或治療建議。如果使用者提到想傷害自己或很痛苦，溫和地建議他立刻聯絡安心專線 1925、生命線 1995 或張老師 1980，緊急時撥 119。
4. 不要在回答中提到「參考資料」這幾個字，直接回答即可。"""


@dataclass
class Chunk:
    doc_title: str
    heading: str
    text: str
    tokens: Counter = field(default_factory=Counter)
    length: int = 0

    @property
    def title(self) -> str:
        return f"{self.doc_title}｜{self.heading}"


@dataclass
class Answer:
    question: str
    answer: str
    sources: list[Chunk]
    mode: str  # "llm"：模型生成；"retrieval"：只檢索；"none"：找不到相關段落
    crisis: bool = False
    error: str | None = None

    @property
    def found(self) -> bool:
        return bool(self.sources)


# ---------------------------------------------------------------------------
# 斷詞與知識庫
# ---------------------------------------------------------------------------

_CJK = re.compile(r"[一-鿿]+")
_WORD = re.compile(r"[a-z0-9]+")


def clean_query(query: str) -> str:
    return QUESTION_WORDS.sub(" ", query)


def tokenize(text: str) -> list[str]:
    """中文用相鄰兩字（bigram），單一個中文字也保留；英文數字用整個單字。"""
    text = text.lower()
    tokens: list[str] = []
    for run in _CJK.findall(text):
        if len(run) == 1:
            tokens.append(run)
        tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    tokens.extend(_WORD.findall(text))
    return tokens


def load_chunks(help_dir: Path = HELP_DIR) -> list[Chunk]:
    """把每份 Markdown 依「## 小標題」切成段落。"""
    chunks: list[Chunk] = []
    for path in sorted(help_dir.glob("*.md")):
        doc_title = path.stem.split("-", 1)[-1]
        heading, lines = None, []

        def flush():
            body = "\n".join(lines).strip()
            if heading and body:
                # 小標題重複一次，讓標題裡的詞權重高一點
                toks = tokenize(f"{heading} {heading} {body}")
                chunks.append(Chunk(doc_title, heading, body, Counter(toks), len(toks)))

        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("## "):
                flush()
                heading, lines = line[3:].strip(), []
            elif line.startswith("# "):
                doc_title = line[2:].strip()
            else:
                lines.append(line)
        flush()
    return chunks


class Retriever:
    """BM25 檢索。"""

    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.k1, self.b = k1, b
        n = len(chunks)
        self.avg_len = sum(c.length for c in chunks) / n if n else 0
        df = Counter()
        for c in chunks:
            df.update(c.tokens.keys())
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    @staticmethod
    def coverage(query: str, chunk: Chunk) -> float:
        """問題的詞有多少比例出現在段落裡。"""
        q_tokens = set(tokenize(clean_query(query)))
        if not q_tokens:
            return 0.0
        return sum(1 for t in q_tokens if t in chunk.tokens) / len(q_tokens)

    @staticmethod
    def in_heading(query: str, chunk: Chunk) -> bool:
        """問題的關鍵字是否出現在段落的小標題裡（強訊號）。"""
        heading = set(tokenize(chunk.heading))
        return any(t in heading for t in tokenize(clean_query(query)) if len(t) >= 2)

    def search(self, query: str, top_k: int = TOP_K) -> list[tuple[Chunk, float]]:
        q_tokens = set(tokenize(clean_query(query)))
        scored = []
        for c in self.chunks:
            score = 0.0
            for t in q_tokens:
                tf = c.tokens.get(t)
                if not tf:
                    continue
                norm = tf + self.k1 * (1 - self.b + self.b * c.length / self.avg_len)
                score += self.idf[t] * tf * (self.k1 + 1) / norm
            if score > 0:
                scored.append((c, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]


_retriever: Retriever | None = None


def get_retriever() -> Retriever:
    global _retriever
    if _retriever is None:
        _retriever = Retriever(load_chunks())
    return _retriever


# ---------------------------------------------------------------------------
# 生成：本機 Ollama
# ---------------------------------------------------------------------------


class LLMUnavailable(Exception):
    """連不到模型或模型回傳錯誤。"""


def call_ollama(system: str, user: str) -> str:
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "options": {"temperature": 0.2},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    # 不經過系統代理伺服器，直接連本機的 Ollama
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=OLLAMA_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        if e.code == 404:
            raise LLMUnavailable(
                f"Ollama 找不到模型「{OLLAMA_MODEL}」，請用 ollama list 確認名稱，"
                f"並設定環境變數 OLLAMA_MODEL"
            ) from e
        raise LLMUnavailable(f"Ollama 回傳錯誤 {e.code}：{detail}") from e
    except TimeoutError as e:
        raise LLMUnavailable(f"等 Ollama 回答超過 {OLLAMA_TIMEOUT:.0f} 秒，模型可能還在載入，請再試一次") from e
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, TimeoutError) or "timed out" in str(reason):
            raise LLMUnavailable(f"等 Ollama 回答超過 {OLLAMA_TIMEOUT:.0f} 秒，模型可能還在載入，請再試一次") from e
        raise LLMUnavailable(f"連不到 Ollama（{OLLAMA_HOST}），請確認 Ollama 有在執行") from e
    except json.JSONDecodeError as e:
        raise LLMUnavailable("Ollama 回傳的格式無法解析") from e

    content = (data.get("message") or {}).get("content", "").strip()
    if not content:
        raise LLMUnavailable("模型沒有產生回答")
    return content


def build_user_prompt(question: str, sources: list[Chunk]) -> str:
    refs = "\n\n".join(
        f"[{i}] {c.title}\n{c.text}" for i, c in enumerate(sources, start=1)
    )
    return f"參考資料：\n{refs}\n\n使用者的問題：{question}"


# ---------------------------------------------------------------------------
# 對外介面
# ---------------------------------------------------------------------------


def is_crisis(text: str) -> bool:
    return any(k in text for k in CRISIS_KEYWORDS)


def answer_question(question: str, use_llm: bool | None = None) -> Answer:
    question = question.strip()
    crisis = is_crisis(question)
    retriever = get_retriever()
    # 相關的條件：關鍵字覆蓋率夠，而且分數夠高、或關鍵字出現在小標題
    ranked = [
        (c, score) for c, score in retriever.search(question)
        if retriever.coverage(question, c) >= MIN_COVERAGE
        and (score >= MIN_SCORE or retriever.in_heading(question, c))
    ]
    hits = []
    if ranked:
        top = ranked[0][1]
        hits = [c for c, score in ranked if score >= top * RELATIVE_CUTOFF]
    if crisis:
        # 提到想傷害自己時，一定把求助專線那段放在最前面
        help_chunk = next(c for c in get_retriever().chunks if c.heading == CRISIS_HEADING)
        hits = [help_chunk] + [c for c in hits if c is not help_chunk]

    if not hits:
        return Answer(
            question, "說明文件裡找不到和這個問題相關的內容。"
            "你可以換個說法再問一次，或直接送出問題回報，由管理員回覆你。",
            [], "none", crisis,
        )

    if use_llm is None:
        use_llm = RAG_USE_LLM
    error = None
    if use_llm:
        try:
            text = call_ollama(SYSTEM_PROMPT, build_user_prompt(question, hits))
            return Answer(question, text, hits, "llm", crisis)
        except LLMUnavailable as e:
            error = str(e)

    # 退回只檢索：直接顯示最相關的段落
    return Answer(question, hits[0].text, hits, "retrieval", crisis, error)
