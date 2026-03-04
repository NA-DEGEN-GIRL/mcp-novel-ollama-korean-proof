"""
Ollama 기반 한글 교정 MCP 서버.
로컬 Ollama 모델을 사용해 소설 원고의 오탈자, 문법 오류, 어색한 표현을 JSON으로 검출한다.
"""

import subprocess
import json
import re
import os
from pathlib import Path
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("ollama-proofreader")

NOVEL_ROOT = os.environ.get("NOVEL_ROOT", "/root/novel")
OLLAMA_PATH = os.environ.get("OLLAMA_PATH", "/usr/local/bin/ollama")
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "gpt-oss-safeguard:20b")

SYSTEM_PROMPT = """[시스템 지침]
너는 한국어 소설 원고의 교정 전문가다. 아래 원고에서 오탈자, 문법 오류, 어색한 표현, 띄어쓰기 오류를 찾아라.

[출력 규칙 — 반드시 준수]
1. 출력은 반드시 JSON 배열이다. [ 로 시작하고 ] 로 끝난다.
2. 마크다운 기호(```, ---, **), 서론, 결론, 설명, 생각 과정을 절대 출력하지 마라.
3. 오류가 없으면 빈 배열 [] 을 출력하라.
4. 각 항목은 아래의 정확한 키를 사용한다.

[JSON 스키마]
{
  "category": "오탈자 | 문법 | 띄어쓰기 | 어색한표현 | 숫자표기 | 조사 | 문장부호 | 반복표현",
  "severity": "error | warning | info",
  "location": "오류가 포함된 문장 또는 주변 5어절 (원문 그대로 인용)",
  "original": "오류가 있는 정확한 구문",
  "suggestions": [
    {"text": "수정안 1 (가장 권장)", "note": "왜 이 수정이 적절한지"},
    {"text": "수정안 2 (대안)", "note": "어떤 맥락에서 이 대안이 나은지"}
  ],
  "reason": "오류의 유형과 원인을 한 문장으로 설명"
}

[severity 기준]
- error: 명백한 오탈자, 문법 오류, 조사 오류. 독자가 즉시 알아챈다.
- warning: 문법적으로 틀리지는 않지만 어색하거나 개선 가능. 번역투, 반복.
- info: 스타일 선택 영역이지만 대안을 알려주면 좋은 것.

[category별 검수 포인트]
- 오탈자: 글자 누락/중복/순서바뀜, 동음이의어 혼동(맞다/맞히다, 되다/돼다, 않다/안다, 웬/왠, 로서/로써)
- 문법: 피동/사동 이중(잡혀지다→잡히다), 주술 호응, 시제 혼란, ㅂ/ㄷ/르 불규칙 활용 오류(돕며→도우며, 걷며→걸으며)
- 띄어쓰기: 의존명사(할 수, 할 뿐, 그럴 리), 보조용언, 고유명사
- 어색한표현: 번역투(~하는 것이었다, 그의 눈은 ~을 보았다), 과잉 서술, 이중 비유
- 숫자표기: 시대 배경에 맞지 않는 숫자 표기 (비현대: 아라비아숫자 금지 / 현대: 수사 혼용 오류)
- 조사: 받침 유무에 따른 을/를, 은/는, 이/가, 과/와 혼동
- 문장부호: 말줄임표/대시/따옴표 불일치, 물음표 뒤 띄어쓰기
- 반복표현: 3문장 이내 동일 단어 반복, 동일 문장 패턴 3회+ 연속

[교정하지 않는 것]
- 큰따옴표("") 안의 대사: 캐릭터 말투로 설정된 비문, 축약, 사투리
- 작은따옴표('') 안의 내면 독백: 캐릭터 성격에 맞는 구어체
- 세계관 고유명사, 기술명, 마법명 (일관성만 검증)
- 문체 선택 ("~했다" vs "~였다" 등)"""


def _find_novel_id(file_path: str) -> str | None:
    """파일 경로에서 소설 ID (no-title-XXX)를 추출한다."""
    match = re.search(r"(no-title-\d+)", file_path)
    return match.group(1) if match else None


def _read_file_safe(path: str, max_lines: int = 0) -> str:
    """파일을 안전하게 읽는다. 없으면 빈 문자열 반환."""
    try:
        text = Path(path).read_text(encoding="utf-8")
        if max_lines > 0:
            lines = text.split("\n")[:max_lines]
            return "\n".join(lines)
        return text
    except (FileNotFoundError, PermissionError):
        return ""


def _extract_body(text: str) -> str:
    """EPISODE_META 이전의 본문만 추출한다."""
    marker = "### EPISODE_META"
    idx = text.find(marker)
    if idx != -1:
        # marker 바로 이전의 --- 구분선도 제거
        body = text[:idx].rstrip()
        if body.endswith("---"):
            body = body[:-3].rstrip()
        return body
    return text


def _get_context(novel_id: str) -> tuple[str, str]:
    """소설 설정에서 세계관 맥락과 캐릭터 말투를 추출한다."""
    novel_dir = os.path.join(NOVEL_ROOT, novel_id)

    # 세계관 맥락
    world = _read_file_safe(os.path.join(novel_dir, "settings/04-worldbuilding.md"), 20)
    world_summary = ""
    for line in world.split("\n"):
        if "시대" in line or "배경" in line or "세계관" in line:
            world_summary += line.strip() + " "
    if not world_summary:
        world_summary = "정보 없음"

    # 캐릭터 말투
    chars = _read_file_safe(os.path.join(novel_dir, "settings/03-characters.md"))
    speech_patterns = []
    current_char = ""
    for line in chars.split("\n"):
        if line.startswith("### ") and "(" in line:
            current_char = line.replace("### ", "").strip()
        if "말투" in line and current_char:
            speech_patterns.append(f"- {current_char}: {line.strip()}")
    speech_text = "\n".join(speech_patterns) if speech_patterns else "정보 없음"

    return world_summary.strip(), speech_text


def _run_ollama(prompt: str, model: str, timeout: int = 300) -> str:
    """Ollama를 실행하고 출력을 반환한다. 프롬프트는 stdin으로 전달."""
    cmd = [OLLAMA_PATH, "run", model, "--hidethinking"]
    try:
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "TERM": "dumb"},  # ANSI 코드 방지
        )
        output = result.stdout
        # ANSI escape 코드 제거
        output = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', output)
        # spinner 문자 제거 (⠙⠹⠸ 등)
        output = re.sub(r'[⠀-⣿]', '', output)
        # 마크다운 코드블록 기호 제거
        output = re.sub(r'^```\w*\s*$', '', output, flags=re.MULTILINE)
        # 빈 줄 정리
        output = re.sub(r'\n{3,}', '\n\n', output).strip()
        return output
    except subprocess.TimeoutExpired:
        return '{"error": "Ollama 응답 시간 초과 (5분)"}'
    except FileNotFoundError:
        return f'{{"error": "Ollama를 찾을 수 없습니다: {OLLAMA_PATH}"}}'
    except Exception as e:
        return f'{{"error": "{str(e)}"}}'


def _parse_json(raw: str) -> list[dict]:
    """Ollama 출력에서 JSON 배열을 추출한다."""
    # 1차: 전체를 JSON으로 파싱
    try:
        result = json.loads(raw)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    # 2차: [ ... ] 패턴 추출
    match = re.search(r'\[.*\]', raw, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group())
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    # 3차: 줄 단위로 { } 객체 추출
    objects = []
    for match in re.finditer(r'\{[^{}]+\}', raw):
        try:
            obj = json.loads(match.group())
            objects.append(obj)
        except json.JSONDecodeError:
            continue
    if objects:
        return objects

    return []


def _format_suggestions(suggestions) -> str:
    """suggestions 배열을 표시용 문자열로 변환한다."""
    if not suggestions:
        return "—"
    if isinstance(suggestions, str):
        return suggestions
    if isinstance(suggestions, list):
        if len(suggestions) == 1:
            item = suggestions[0]
            if isinstance(item, dict):
                return item.get("text", str(item))
            return str(item)
        parts = []
        for i, item in enumerate(suggestions, 1):
            if isinstance(item, dict):
                text = item.get("text", str(item))
            else:
                text = str(item)
            parts.append(f"\u2460\u2461\u2462\u2463\u2464"[i-1] + " " + text if i <= 5 else f"({i}) {text}")
        return " / ".join(parts)
    return str(suggestions)


def _save_report(report: str, file_path: str, novel_id: str | None) -> str:
    """보고서를 소설 폴더 내 reviews/에 저장하고 경로를 반환한다."""
    if not novel_id:
        return ""
    novel_dir = os.path.join(NOVEL_ROOT, novel_id)
    reviews_dir = os.path.join(novel_dir, "reviews")
    os.makedirs(reviews_dir, exist_ok=True)

    # 파일명: chapter-01.md → chapter-01-proofread.md
    basename = Path(file_path).stem  # chapter-01
    out_name = f"{basename}-proofread.md"
    out_path = os.path.join(reviews_dir, out_name)

    Path(out_path).write_text(report, encoding="utf-8")
    return out_path


def _format_report(items: list[dict], file_path: str, model: str) -> str:
    """검출 목록을 마크다운 보고서로 변환한다."""
    errors = [x for x in items if x.get("severity") == "error"]
    warnings = [x for x in items if x.get("severity") == "warning"]
    infos = [x for x in items if x.get("severity") == "info"]
    # severity가 없는 항목은 warning으로 처리
    unclassified = [x for x in items if x.get("severity") not in ("error", "warning", "info")]
    warnings.extend(unclassified)

    total = len(items)

    lines = [
        f"## Ollama 한글 교정 결과",
        f"",
        f"- 모델: `{model}`",
        f"- 대상: `{file_path}`",
        f"- 총 지적: **{total}건** (❌ {len(errors)} / ⚠️ {len(warnings)} / 💡 {len(infos)})",
        f"",
    ]

    if not items:
        lines.append("교정 지적 사항이 없습니다. ")
        return "\n".join(lines)

    def _add_table(title: str, group: list[dict]):
        if not group:
            return
        lines.append(f"### {title}")
        lines.append("")
        lines.append("| # | 분류 | 원문 | 수정안 | 사유 |")
        lines.append("|---|------|------|--------|------|")
        for i, item in enumerate(group, 1):
            cat = item.get("category", "—")
            orig = item.get("original", "—")
            sugg = _format_suggestions(item.get("suggestions", item.get("suggestion", "—")))
            reason = item.get("reason", "—")
            lines.append(f"| {i} | {cat} | {orig} | {sugg} | {reason} |")
        lines.append("")

    _add_table("❌ error (반드시 수정)", errors)
    _add_table("⚠️ warning (수정 권장)", warnings)
    _add_table("💡 info (참고)", infos)

    return "\n".join(lines)


@mcp.tool()
def proofread(file_path: str, model: str = "", timeout: int = 300) -> str:
    """소설 에피소드 파일을 Ollama로 한글 교정 검수한다.

    Args:
        file_path: 에피소드 파일 절대 경로. 예: "/root/novel/no-title-011/chapters/prologue/chapter-01.md"
        model: Ollama 모델명. 비어있으면 기본값(gpt-oss-safeguard:20b) 사용.
        timeout: Ollama 응답 대기 시간(초). 기본 300초(5분).
    """
    if not model:
        model = DEFAULT_MODEL

    # 파일 읽기
    text = _read_file_safe(file_path)
    if not text:
        return f"파일을 읽을 수 없습니다: {file_path}"

    body = _extract_body(text)
    if len(body) < 50:
        return f"본문이 너무 짧습니다 ({len(body)}자). 파일을 확인하세요."

    # 소설 설정 로드
    novel_id = _find_novel_id(file_path)
    world_ctx = "정보 없음"
    speech_ctx = "정보 없음"
    if novel_id:
        world_ctx, speech_ctx = _get_context(novel_id)

    # 프롬프트 구성
    prompt = SYSTEM_PROMPT + f"""

[세계관 맥락]
{world_ctx}

[캐릭터 말투 목록 — 아래 패턴은 의도적 비문이므로 교정하지 마라]
{speech_ctx}

[원고 시작]
{body}
[원고 끝]

위 원고를 검수하여 JSON 배열을 출력하라. [ 로 시작해라."""

    # Ollama 실행
    raw = _run_ollama(prompt, model, timeout)

    # 에러 체크
    if raw.startswith('{"error"'):
        try:
            err = json.loads(raw)
            return f"Ollama 실행 오류: {err['error']}"
        except Exception:
            return f"Ollama 실행 오류: {raw}"

    # JSON 파싱
    items = _parse_json(raw)

    if not items and raw.strip():
        # 파싱 실패 — 원본 출력 포함하여 반환
        return (
            f"JSON 파싱 실패. Ollama 원본 출력:\n\n"
            f"```\n{raw[:2000]}\n```\n\n"
            f"모델을 변경하거나 프롬프트를 조정해 보세요."
        )

    # 보고서 생성 및 저장
    report = _format_report(items, file_path, model)
    saved_path = _save_report(report, file_path, novel_id)
    if saved_path:
        report += f"\n\n---\n저장됨: `{saved_path}`"
    return report


@mcp.tool()
def proofread_text(text: str, world_context: str = "", speech_patterns: str = "", model: str = "", timeout: int = 300) -> str:
    """텍스트를 직접 전달하여 Ollama로 한글 교정 검수한다. 파일 없이 텍스트만 검수할 때 사용.

    Args:
        text: 검수할 원고 텍스트.
        world_context: 세계관 맥락 (예: "현대 한국 + 던전"). 비어있으면 "정보 없음" 사용.
        speech_patterns: 캐릭터 말투 목록. 비어있으면 "정보 없음" 사용.
        model: Ollama 모델명. 비어있으면 기본값 사용.
        timeout: 응답 대기 시간(초). 기본 300초.
    """
    if not model:
        model = DEFAULT_MODEL

    if len(text) < 50:
        return f"텍스트가 너무 짧습니다 ({len(text)}자)."

    prompt = SYSTEM_PROMPT + f"""

[세계관 맥락]
{world_context or '정보 없음'}

[캐릭터 말투 목록]
{speech_patterns or '정보 없음'}

[원고 시작]
{text}
[원고 끝]

위 원고를 검수하여 JSON 배열을 출력하라. [ 로 시작해라."""

    raw = _run_ollama(prompt, model, timeout)

    if raw.startswith('{"error"'):
        try:
            err = json.loads(raw)
            return f"Ollama 실행 오류: {err['error']}"
        except Exception:
            return f"Ollama 실행 오류: {raw}"

    items = _parse_json(raw)

    if not items and raw.strip():
        return (
            f"JSON 파싱 실패. Ollama 원본 출력:\n\n"
            f"```\n{raw[:2000]}\n```"
        )

    report = _format_report(items, "(직접 입력)", model)
    return report


@mcp.tool()
def proofread_raw(file_path: str, model: str = "", timeout: int = 300) -> str:
    """소설 에피소드 파일을 Ollama로 검수하고, 파싱 전 JSON 원본을 반환한다. 디버깅용.

    Args:
        file_path: 에피소드 파일 절대 경로.
        model: Ollama 모델명. 비어있으면 기본값 사용.
        timeout: 응답 대기 시간(초). 기본 300초.
    """
    if not model:
        model = DEFAULT_MODEL

    text = _read_file_safe(file_path)
    if not text:
        return f"파일을 읽을 수 없습니다: {file_path}"

    body = _extract_body(text)
    novel_id = _find_novel_id(file_path)
    world_ctx = "정보 없음"
    speech_ctx = "정보 없음"
    if novel_id:
        world_ctx, speech_ctx = _get_context(novel_id)

    prompt = SYSTEM_PROMPT + f"""

[세계관 맥락]
{world_ctx}

[캐릭터 말투 목록]
{speech_ctx}

[원고 시작]
{body}
[원고 끝]

위 원고를 검수하여 JSON 배열을 출력하라. [ 로 시작해라."""

    raw = _run_ollama(prompt, model, timeout)
    return f"모델: {model}\n파일: {file_path}\n\n--- Ollama 원본 출력 ---\n{raw}"


@mcp.tool()
def list_models() -> str:
    """사용 가능한 Ollama 모델 목록을 반환한다."""
    try:
        result = subprocess.run(
            [OLLAMA_PATH, "list"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "TERM": "dumb"},
        )
        output = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', result.stdout)
        return f"사용 가능한 모델:\n\n{output}"
    except FileNotFoundError:
        return f"Ollama를 찾을 수 없습니다: {OLLAMA_PATH}"
    except Exception as e:
        return f"오류: {e}"


if __name__ == "__main__":
    mcp.run()
