# 학습 준비 현황

2026-11-23 기준. 데이터 출처와 라이선스는 [`data/README.md`](../data/README.md).

## 욕설 모델 (KcELECTRA, MIT)

| 준비물 | 상태 | 남은 일 | 담당 |
|---|---|---|---|
| 공개 학습 데이터 | 완료, 79,288문장 | 없음 | |
| 공개 평가 데이터 | 완료, 24,667문장 | 없음 | |
| 학습 코드 (문맥 포함) | 완료 | 없음 | 파트 1 |
| 문맥 합성 데이터 | 생성기 완료 | 20개 품질 확인 후 전체 생성 | 파트 1 |
| 우회 변형 규칙 | **1/10종** | 나머지 9종 구현 후 학습 데이터 재생성 | 파트 3 |
| D셋 (오탐 유도) | 초안 100개 | 사람이 검수 후 확정 | 파트 3 |
| C셋 (사람 우회) | **샘플 5개** | 필터 뚫기 챌린지로 100~200개 수집 | 파트 3 |
| 사전 필터 | 미완성 | 정규화, 단어 목록, 화이트리스트 (D 초안 오탐률 28%) | 파트 3 |

```sh
uv run python ml/moderation/prepare_public.py        # 공개 데이터 받기, 변환
uv run python ml/moderation/synth_context.py --n 20 --out data/moderation/raw/context_synth.jsonl
uv run python ml/moderation/build_train.py           # train.jsonl 생성
# GPU에서 ml/moderation/train.py → export_onnx.py (방법은 각 파일 맨 위)
uv run python eval/moderation/score.py data/moderation/test_D_fp/draft_v1.jsonl --label kcelectra-v1
uv run python eval/moderation/score.py data/moderation/eval_only/kmhas_test.jsonl --label kcelectra-v1
```

현재 데이터로 빌드하면 약 30만 줄(욕설 20%)이다. 변형 규칙이 1종뿐이라 지금 만든 `train.jsonl`로 학습하면 우회 표현 성능이 낮게 나온다.

## 일정 모델 (소형 LLM + LoRA)

| 준비물 | 상태 | 남은 일 | 담당 |
|---|---|---|---|
| 합성 학습 데이터 | 생성 중, 목표 3,000개 | 완료 대기 | 파트 1 |
| 날짜 정규화 | **예시 4/8 통과** | 8.5 규칙 전부 구현 | 파트 4 |
| 파일럿 테스트셋 20개 | **없음** | 8.6 유형대로 작성 | 파트 4 |
| 실제 테스트셋 100~150개 | **없음** | 역할극 후 2인 라벨링 (사람이 직접) | 파트 4 |

날짜 정규화 결과가 학습 프롬프트에 그대로 들어간다. 규칙을 완성하기 전에 LoRA를 학습하면 서빙 때와 입력이 달라지므로, **날짜 정규화부터 끝내고 학습한다.**

기본 베이스 모델은 `Qwen/Qwen2.5-3B-Instruct`다. Qwen Research License라 수업, 연구 용도만 가능하고 상업 서비스로 내려면 별도 허가가 필요하다.
