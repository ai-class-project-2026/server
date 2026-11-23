# data

| 폴더 | 내용 | 커밋 |
|---|---|---|
| `schedule/pilot/` | 파일럿 20개 (1주차 가설 검증) | O |
| `schedule/real_test/` | 실제 역할극 테스트셋, **학습 금지** | O |
| `schedule/synth/` | 합성 학습 데이터 (`generate.py`로 재생성) | X |
| `moderation/raw/` | 공개 학습 데이터 변환본, 문맥 합성 데이터 (원본은 `raw/_sources/`) | X |
| `moderation/eval_only/` | 공개 평가 데이터, **학습 금지** | X |
| `moderation/test_C_human/` | 필터 뚫기 챌린지 | O |
| `moderation/test_D_fp/` | 오탐 유도셋 (`draft_v1.jsonl`은 사람 검수 전 초안) | O |

각 줄 형식은 server README의 "일정 AI 학습/평가" 참고. 전체 준비 현황과 실행 순서는 [`ml/TRAINING.md`](../ml/TRAINING.md).

## 욕설 공개 데이터

`uv run python ml/moderation/prepare_public.py`로 고정된 버전을 받아 변환한다. 라벨은 기획서 9.8을 따라 실제 욕설만 1로 두고, 욕 없는 혐오나 암시 표현은 뺀다.

| 데이터 | 라이선스 | 용도 | 변환 기준 | 건수 (욕설/정상) |
|---|---|---|---|---|
| [Curse-detection-data](https://github.com/2runo/Curse-detection-data) | MIT | 학습 | 원래 라벨 | 2,039 / 3,779 |
| [K-MHaS](https://github.com/adlnlp/K-MHaS) train, valid | [CC BY-SA 4.0](https://huggingface.co/datasets/jeanlee/kmhas_korean_hate_speech) | 학습 | Profanity=1, Not Hate=0 | 12,911 / 47,721 |
| [BEEP!](https://github.com/kocohub/korean-hate-speech) | CC BY-SA 4.0 | 학습 | `none`만 정상으로 사용 | 0 / 3,646 |
| [KOTOX](https://huggingface.co/datasets/ssgyejin/KOTOX) train, valid | MIT | 학습 | 명시적 욕이 있는 문장=1, 중립 문장과 그 변형=0 | 940 / 8,252 |
| K-MHaS test | CC BY-SA 4.0 | 평가 A | 위와 같음 | 3,218 / 11,817 |
| KOTOX test | MIT | 우회 평가 | 위와 같음 | 112 / 919 |
| [UnSmile](https://github.com/smilegate-ai/korean_unsmile_dataset) | CC BY-NC-ND 4.0 | 평가 전용 | `악플/욕설`=1, `clean`=0 | 3,928 / 4,673 |

* UnSmile은 변경 금지 조건이라 학습, 증강, 재배포하지 않는다. `악플/욕설`에는 욕이 없는 악플도 섞여 있어 참고 지표로만 본다
* CC BY-SA 데이터로 만든 결과를 공개할 때는 출처를 표시하고 라이선스 조건을 확인한다

쓰지 않은 데이터:

| 데이터 | 이유 |
|---|---|
| [KOLD](https://github.com/boychaboy/KOLD) | 라이선스 명시가 없음 |
| [K-HATERS](https://github.com/ssu-humane/K-HATERS) | CC BY 4.0으로 사용 가능하나 혐오 강도 라벨이라 9.8 기준과 다름 |
| [DKTC](https://github.com/tunib-ai/DKTC) | CC BY-NC-SA 4.0, 대화 전체에 붙은 위협 라벨이라 메시지별 판정에 맞지 않음 |
| [한국어 악성댓글](https://github.com/ZIZUN/korean-malicious-comments-dataset) | MIT이나 욕 없는 악플과 욕설이 구분되지 않음 |
