# 開發規約（必讀）

本規約適用於 `piano-midi-cleanup` **整個工程**，不是只適用於分手、多手或任何單一模組。所有開發者與 Agent 在修改程式前都必須閱讀並遵守。

## 1. 分支與提交

- **禁止建立任何分支。** 本工程只在既有 `main` 上工作。
- 完成使用者要求的修改後，預設直接 commit 並 push；若使用者明確要求只實驗、不要提交或不要推送，則不得提交或推送。
- 每個 commit 只處理一個明確目的；禁止把不相關的重構、清理、功能修改混在同一提交。
- 不得以「順手整理」「順便重構」為理由擴大修改範圍。

## 2. 先理解語義，再改程式

修改既有核心邏輯前，必須先確認：

1. 這段程式原本在解決什麼問題；
2. 它的輸入、輸出與行為 invariant 是什麼；
3. 相關邏輯最初在哪個 commit 引入、後續為什麼修改；
4. 本次任務是否真的要求改變原有語義。

如果意圖不清楚，**先查 git history、spec、README、事故報告與既有測試，再動手。**

不得因為目前資料結構、命名或 ownership 看起來更整齊，就自行重新解釋演算法原本的目的。

## 3. 結構修正與行為修改必須分離

以下類型的修改原則上必須分開：

- 命名 / channel / topology / 資料模型修正；
- cost / objective / heuristic / threshold / 搜尋策略修改；
- UI / CLI / pipeline wiring；
- 純重構與實際行為變更。

如果一個結構修正不可避免地會改變演算法行為，必須在提交前明確寫出：

- 舊語義；
- 新語義；
- 為什麼必須改；
- 預期哪些輸出會變；
- 用什麼 regression test / A/B comparison 驗證。

**禁止把 semantic change 偽裝成 refactor 或 bug fix。**

## 4. 保護行為 invariant

對核心演算法，除了「程式能跑」與單元測試外，必須保護其真正的業務 / 音樂語義 invariant。

修改前先把最重要的 invariant 寫清楚；修改後至少以其中一種方式固定：

- synthetic regression test；
- golden fixture；
- representative real-world sample A/B comparison；
- 明確的 spec / assertion。

只驗證 channel、型別、函式返回值或 pipeline 能執行，不足以證明核心演算法沒有退化。

## 5. 演算法修改必須做代表性 A/B

任何可能改變輸出的演算法修改，都必須在提交前比較修改前後的代表性結果。

至少記錄與本次修改直接相關的指標；若結果有大幅變化，必須先停下來究明原因，不能因為 tests 全綠就直接合入。

對 MIDI / 音樂演算法尤其如此：可生成「看起來合理」的輸出，不代表原設計語義仍然成立。

## 6. 禁止用新 heuristic 掩蓋未查明的 regression

發現結果退化時，處理順序必須是：

1. 找出第一個發生變化的版本 / commit；
2. 確認是搜尋問題、objective 問題、資料問題還是 semantic regression；
3. 找到 root cause；
4. 再決定最小修復。

在 root cause 未確認前，禁止持續疊加新的 cost、reward、threshold、lookahead、例外規則或模型來「把結果調回去」。

## 7. 修 bug 時禁止擅自改原設計目的

Bug fix 的目標是修正已確認的錯誤，不是重新設計相鄰演算法。

若修正 A 需要同時改變 B 的語義，必須把 B 視為獨立設計變更處理，而不是跟著 A 一起修改。

尤其要避免：

> 因為資料結構或角色 mapping 改了，所以順便重新定義 cost function / heuristic 的含義。

這類修改必須有獨立證據與測試。

## 8. 語義角色優先於位置索引

當 slice index、channel index、array position 同時承擔「音高位置」「演奏者」「手」「聲部」等不同語義時，優先使用明確命名 / mapping。

避免讓 `[-1]`、`[:2]`、`[2:]` 這類純位置寫法暗中承擔跨層級的業務語義。若確實使用，必須在附近註明 invariant。

## 9. 不得破壞歷史設計理由

重要 heuristic / algorithm 的註釋與 spec 不只要記「怎麼算」，還要記：

- 它要回答什麼問題；
- 它不應回答什麼問題；
- 哪些行為是不可破壞的；
- 哪些參數只是實作細節。

重構可以改程式結構，但不得在沒有明確提案與驗證的情況下改變這些設計契約。

spec 中標示「本程式特性，不要改」的內容是設計契約，不得當成重構細節；變更前必須提出獨立設計變更、說明語義差異，並補上行為回歸測試。

## 10. 測試資料與實驗

- MIDI 測試資料放在獨立 private sibling repo `../piano-midi-cleanup-data/`。
- 原始 / baseline MIDI 不得被實驗直接覆寫；實驗使用副本或臨時輸出。
- 重新處理資料前，記錄應用程式 revision 與處理參數。
- 臨時 debug script、比較輸出、render、installer、machine-specific shortcut 不得提交到主 repo。

## 11. 事故處理

一旦確認核心語義 regression：

- 停止繼續疊加補償性設計；
- 找到事故起點 commit；
- 寫明直接原因、root cause、Five Whys、影響、為何未被測試抓到、修正原則與再發防止；
- 將新的工程教訓寫回本規約；
- 補上能直接防止同類事故的 regression test。

本工程第一份重大 semantic regression 事故報告：

- [2026-10-26：Blob 語義退化事故報告](INCIDENT-2026-10-26-BLOB-SEMANTIC-REGRESSION.md)

## 12. 最小變更原則

任何任務都優先採用能完成目標的最小修改。

如果任務只要求修一個 bug，就不要重新設計整個模組；如果只要求驗證一個假設，就先做臨時實驗，不要直接把實驗架構正式化。

**能清楚證明原因後再改，比快速堆出一個看似合理的結果更重要。**
