"""
Ollama 기반 한글 교정 MCP 서버.
로컬 Ollama 모델을 사용해 소설 원고의 오탈자, 문법 오류, 어색한 표현을 검출한다.
"""

import subprocess
import re
import os
from datetime import datetime
from pathlib import Path
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("ollama-proofreader")

NOVEL_ROOT = os.environ.get("NOVEL_ROOT", "/root/novel")
OLLAMA_PATH = os.environ.get("OLLAMA_PATH", "/usr/local/bin/ollama")
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "gpt-oss-safeguard:20b")

SYSTEM_PROMPT = """너는 한국어 소설 원고의 교정 전문가다. 아래 원고에서 오탈자, 문법 오류, 어색한 표현, 띄어쓰기 오류를 찾아라.

[출력 형식 — 반드시 마크다운 표로 출력]

아래 형식의 마크다운 표를 출력하라. 표 외에 다른 텍스트(서론, 결론, 설명, 생각 과정)를 절대 출력하지 마라.

### ❌ error (반드시 수정)

| # | 분류 | 원문 | 수정안 | 사유 |
|---|------|------|--------|------|
| 1 | 오탈자 | 되서 | 돼서 | '되다+어서'의 축약형은 '돼서' |

### ⚠️ warning (수정 권장)

| # | 분류 | 원문 | 수정안 | 사유 |
|---|------|------|--------|------|
| 1 | 번역투 | ~하는 것이었다 | ~했다 | 불필요한 우회 표현 |

### 💡 info (참고)

| # | 분류 | 원문 | 수정안 | 사유 |
|---|------|------|--------|------|

[severity 기준]
- ❌ error: 명백한 오탈자, 문법 오류, 조사 오류. 독자가 즉시 알아챈다.
- ⚠️ warning: 문법적으로 틀리지는 않지만 어색하거나 개선 가능. 번역투, 반복.
- 💡 info: 스타일 선택 영역이지만 대안을 알려주면 좋은 것.

[분류 카테고리]
- 오탈자: 글자 누락/중복/순서바뀜, 동음이의어 혼동(맞다/맞히다, 되다/돼다, 않다/안다, 웬/왠, 로서/로써)
- 문법: 피동/사동 이중(잡혀지다→잡히다), 주술 호응, 시제 혼란, 불규칙 활용 오류
- 띄어쓰기: 의존명사(할 수, 할 뿐, 그럴 리), 보조용언, 고유명사
- 어색한표현: 번역투(~하는 것이었다), 과잉 서술, 이중 비유
- 숫자표기: 시대 배경에 맞지 않는 숫자 표기
- 조사: 받침 유무에 따른 을/를, 은/는, 이/가, 과/와 혼동
- 문장부호: 말줄임표/대시/따옴표 불일치, 물음표 뒤 띄어쓰기
- 반복표현: 3문장 이내 동일 단어 반복, 동일 문장 패턴 3회+ 연속

[교정하지 않는 것]
- 큰따옴표("") 안의 대사: 캐릭터 말투로 설정된 비문, 축약, 사투리
- 작은따옴표('') 안의 내면 독백: 캐릭터 성격에 맞는 구어체
- 세계관 고유명사, 기술명, 마법명
- 문체 선택 ("~했다" vs "~였다" 등)

[중요]
- 해당 severity 섹션에 지적이 없으면 그 섹션 전체를 생략하라.
- 오류가 전혀 없으면 "교정 지적 사항 없음." 한 줄만 출력하라.
- 수정안이 여러 개면 "① 수정안1 / ② 수정안2" 형식으로 한 셀에 쓰라."""


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
            env={**os.environ, "TERM": "dumb"},
        )
        output = result.stdout
        # ANSI escape 코드 제거
        output = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', output)
        # spinner 문자 제거 (⠙⠹⠸ 등)
        output = re.sub(r'[⠀-⣿]', '', output)
        # 빈 줄 정리
        output = re.sub(r'\n{3,}', '\n\n', output).strip()
        return output
    except subprocess.TimeoutExpired:
        return "[오류] Ollama 응답 시간 초과 (제한: {}초)".format(timeout)
    except FileNotFoundError:
        return f"[오류] Ollama를 찾을 수 없습니다: {OLLAMA_PATH}"
    except Exception as e:
        return f"[오류] {e}"


def _save_report(report: str, file_path: str, novel_id: str | None) -> str:
    """보고서를 소설 폴더 내 reviews/에 저장하고 경로를 반환한다."""
    if not novel_id:
        return ""
    novel_dir = os.path.join(NOVEL_ROOT, novel_id)
    reviews_dir = os.path.join(novel_dir, "reviews")
    os.makedirs(reviews_dir, exist_ok=True)

    basename = Path(file_path).stem
    out_name = f"{basename}-proofread.md"
    out_path = os.path.join(reviews_dir, out_name)

    Path(out_path).write_text(report, encoding="utf-8")
    return out_path


def _build_header(file_path: str, model: str) -> str:
    """보고서 헤더를 생성한다."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    return (
        f"## Ollama 한글 교정 결과\n\n"
        f"- 모델: `{model}`\n"
        f"- 대상: `{file_path}`\n"
        f"- 일시: {now}\n"
    )


@mcp.tool()
def proofread(file_path: str, model: str = "", timeout: int = 300) -> str:
    """소설 에피소드 파일을 Ollama로 한글 교정 검수한다. 결과는 소설/reviews/에 자동 저장된다.

    Args:
        file_path: 에피소드 파일 절대 경로. 예: "/root/novel/no-title-001/chapters/prologue/chapter-01.md"
        model: Ollama 모델명. 비어있으면 기본값(gpt-oss-safeguard:20b) 사용.
        timeout: Ollama 응답 대기 시간(초). 기본 300초(5분).
    """
    if not model:
        model = DEFAULT_MODEL

    text = _read_file_safe(file_path)
    if not text:
        return f"파일을 읽을 수 없습니다: {file_path}"

    body = _extract_body(text)
    if len(body) < 50:
        return f"본문이 너무 짧습니다 ({len(body)}자). 파일을 확인하세요."

    novel_id = _find_novel_id(file_path)
    world_ctx = "정보 없음"
    speech_ctx = "정보 없음"
    if novel_id:
        world_ctx, speech_ctx = _get_context(novel_id)

    prompt = SYSTEM_PROMPT + f"""

[세계관 맥락]
{world_ctx}

[캐릭터 말투 목록 — 아래 패턴은 의도적 비문이므로 교정하지 마라]
{speech_ctx}

[원고 시작]
{body}
[원고 끝]

위 원고를 검수하여 마크다운 표로 출력하라."""

    raw = _run_ollama(prompt, model, timeout)

    if raw.startswith("[오류]"):
        return raw

    # 헤더 + 모델 출력 합치기
    header = _build_header(file_path, model)
    report = header + "\n" + raw

    # 저장
    saved_path = _save_report(report, file_path, novel_id)
    if saved_path:
        report += f"\n\n---\n저장됨: `{saved_path}`"

    return report


@mcp.tool()
def proofread_text(text: str, world_context: str = "", speech_patterns: str = "", model: str = "", timeout: int = 300) -> str:
    """텍스트를 직접 전달하여 Ollama로 한글 교정 검수한다. 파일 없이 텍스트만 검수할 때 사용.

    Args:
        text: 검수할 원고 텍스트.
        world_context: 세계관 맥락 (예: "현대 한국 + 던전"). 비어있으면 "정보 없음".
        speech_patterns: 캐릭터 말투 목록. 비어있으면 "정보 없음".
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

위 원고를 검수하여 마크다운 표로 출력하라."""

    raw = _run_ollama(prompt, model, timeout)

    if raw.startswith("[오류]"):
        return raw

    header = _build_header("(직접 입력)", model)
    return header + "\n" + raw


@mcp.tool()
def proofread_raw(file_path: str, model: str = "", timeout: int = 300) -> str:
    """소설 에피소드 파일을 Ollama로 검수하고, 모델 원본 출력을 그대로 반환한다. 디버깅용.

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

위 원고를 검수하여 마크다운 표로 출력하라."""

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
