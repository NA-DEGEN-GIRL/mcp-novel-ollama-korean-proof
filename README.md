# mcp-novel-ollama-korean-proof

Ollama 기반 한글 교정 MCP 서버. 로컬 Ollama 모델을 사용해 소설 원고의 오탈자, 문법 오류, 어색한 표현을 JSON으로 검출한다.

[claude-novel-templates](https://github.com/NA-DEGEN-GIRL/claude-novel-templates)의 `ollama-proofreader` 에이전트가 사용하는 MCP 서버.

## 도구

| 도구 | 설명 |
|------|------|
| `proofread` | 파일 경로로 에피소드 교정 (세계관/캐릭터 맥락 자동 로드) |
| `proofread_text` | 텍스트를 직접 전달하여 교정 |
| `proofread_raw` | 교정 후 JSON 원본 반환 (디버깅용) |
| `list_models` | 사용 가능한 Ollama 모델 목록 |

## 설치

```bash
pip install mcp
```

Ollama가 설치되어 있어야 한다:

```bash
ollama pull gpt-oss-safeguard:20b
```

## 설정

`.mcp.json`에 등록:

```json
{
  "mcpServers": {
    "ollama-proofreader": {
      "command": "python3",
      "args": ["/path/to/ollama_proofreader.py"],
      "env": {
        "NOVEL_ROOT": "/path/to/novel"
      }
    }
  }
}
```

### 환경 변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `NOVEL_ROOT` | `/root/novel` | 소설 프로젝트 루트 경로 |
| `OLLAMA_PATH` | `/usr/local/bin/ollama` | Ollama 바이너리 경로 |
| `OLLAMA_MODEL` | `gpt-oss-safeguard:20b` | 기본 모델 |

## 사용 예시

```python
# MCP 도구 호출
proofread(file_path="/root/novel/no-title-001/chapters/arc-01/chapter-07.md")

# 다른 모델 사용
proofread(file_path="...", model="qwen3-coder:30b")

# 텍스트 직접 교정
proofread_text(text="원고 텍스트...", world_context="현대 한국 + 던전")
```

## 관련 프로젝트

- [claude-novel-templates](https://github.com/NA-DEGEN-GIRL/claude-novel-templates) - AI 웹소설 집필 시스템
- [mcp-novel-calc](https://github.com/NA-DEGEN-GIRL/mcp-novel-calc) - 소설 집필용 계산 MCP
- [mcp-novel-hanja](https://github.com/NA-DEGEN-GIRL/mcp-novel-hanja) - 한자 검색/검증 MCP
- [mcp-novelai-image](https://github.com/NA-DEGEN-GIRL/mcp-novelai-image) - NovelAI 이미지 생성 MCP
