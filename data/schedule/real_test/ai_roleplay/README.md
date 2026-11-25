# AI 페르소나 역할극

**사람이 한 대화도, 사람이 단 정답도 아니다.** 2026-11-25에 Claude가 카드의 역할마다 말투를 정해 연기하고, 대화를 쓰지 않은 다른 Claude 두 명이 정답을 달았다.

| 파일 | 내용 |
|---|---|
| `real_unlabeled.jsonl` 120개, `pilot_unlabeled.jsonl` 20개, `hard_unlabeled.jsonl` 24개 | 정답 없는 대화 (R, P, H 카드) |
| `*_labels_a.jsonl`, `*_labels_b.jsonl` | AI 라벨러 두 명이 따로 단 정답 |
| `test_ai.jsonl`, `pilot_ai.jsonl`, `hard_ai.jsonl` | 합친 정답 |
| `reports/` | 이 자료로 잰 점수. `eval/reports/`(발표와 대시보드)와 섞지 않는다 |

* 대화에는 `"source": "ai_persona"`, 정답에는 `"label_source": "ai"`가 있다
* 쓰는 곳: 사람 라벨러의 연습, 평가 도구 점검, "AI 역할극, 사람 테스트셋 아님"이라고 밝힌 참고 점수
* 쓰지 않는 곳: `test.jsonl`, `pilot.jsonl`, `hard.jsonl`, 발표의 최종 점수, 학습 데이터
* 두 라벨러는 확정·취소·제안·폐기와 유형이 164개 모두 같다. 같은 모델이고 대화가 깔끔해서다. **사람 라벨러 일치도로 쓰지 않는다**
* 실제 단톡보다 답이 빠르고 흐름이 카드대로 깔끔해서 사람 대화보다 쉬울 수 있다. 약점 24개 중 10개는 8턴보다 짧다
* 시각(`ts`)이 없다. 모든 말은 `ref_datetime`에 한 것으로 계산된다
