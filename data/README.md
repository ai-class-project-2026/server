# data

| 폴더 | 내용 | 커밋 |
|---|---|---|
| `schedule/pilot/` | 파일럿 20개 (1주차 가설 검증) | O |
| `schedule/real_test/` | 실제 역할극 테스트셋, **학습 금지** | O |
| `schedule/synth/` | 합성 학습 데이터 (`generate.py`로 재생성) | X |
| `moderation/raw/` | 공개 데이터 원본 (라이선스) | X |
| `moderation/test_A_public/` | 공개 데이터 평가셋 (UnSmile), **학습/증강 금지** | X |
| `moderation/test_C_human/` | 필터 뚫기 챌린지 | O |
| `moderation/test_D_fp/` | 오탐 유도셋 | O |

각 줄 형식은 server README의 "일정 AI 학습/평가" 참고. 공개 데이터는 출처와 라이선스를 여기에 적는다.

## 욕설 공개 데이터 (기획서 9.4)

받기: `uv run python ml/moderation/fetch_public.py` → `raw/`, `test_A_public/`에 `{"text", "label", "orig"}` jsonl 생성.
`orig`는 원본 라벨이라 매핑을 나중에 바꿀 수 있다.

| 데이터 | 출처 | 라이선스 | 용도 | 라벨 매핑 | 건수 (욕설/정상) |
|---|---|---|---|---|---|
| Curse-detection-data | [2runo/Curse-detection-data](https://github.com/2runo/Curse-detection-data) `dataset.txt` | MIT | 학습 + 증강 → `raw/curse_2runo.jsonl` | `1` → 1 | 5,825 (2,044 / 3,781) |
| BEEP! korean-hate-speech | [kocohub/korean-hate-speech](https://github.com/kocohub/korean-hate-speech) `labeled/train.tsv`, `dev.tsv` | CC BY-SA 4.0 | 학습 + 증강 → `raw/beep.jsonl` | `hate`, `offensive` → 1, `none` → 0 | 8,367 (4,721 / 3,646) |
| KOLD | [boychaboy/KOLD](https://github.com/boychaboy/KOLD) `data/kold_v1.json` (git LFS) | **명시 없음** (LICENSE 파일, README 모두 없음) → 수업 프로젝트(비상업, 탐지 목적)로 사용, 출처 Jeong et al., EMNLP 2022 인용 | 학습 (2026-11-16 결정) → `raw/kold.jsonl` (`--kold`) | `OFF` True → 1 | 40,429 (20,310 / 20,119) |
| Smilegate UnSmile | [smilegate-ai/korean_unsmile_dataset](https://github.com/smilegate-ai/korean_unsmile_dataset) `unsmile_train/valid_v1.0.tsv` | CC BY-NC-ND 4.0 | **평가 전용** (테스트셋 A) → `test_A_public/unsmile.jsonl` | `악플/욕설` → 1, `clean` → 0, 그 외 혐오만 있는 문장은 제외 | 8,603 (3,929 / 4,674) |

- BEEP의 `hate`/`offensive`는 욕설이 없는 비하 표현도 포함한다. 오탐이 늘면 `orig == "none"`만 정상 문장으로 쓰고 나머지는 빼는 것도 고려.
- UnSmile은 변경 금지(ND) 조건이라 증강하지 않고, 변환한 파일도 커밋하지 않는다 (스크립트로 재생성).
- 출처 표기(BY): 발표 자료/대시보드에 위 데이터 이름과 링크를 적는다.
