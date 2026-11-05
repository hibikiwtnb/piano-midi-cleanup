# DuET 對本專案的啟發：神經網路譜表先驗與規則式 MIDI 分手

研究日期：2026-11-05  
狀態：**原始碼對照研究／實驗建議**，不是已實作功能、實測結論或決定採用的技術方案。

## 1. 結論

DuET（*Note–Frame Dual Encoding for Piano Performance-to-Score Transcription*）的價值，不在於它有一套更強的手工分手演算法，而在於：**先由專門的 Transformer 根據演奏上下文預測譜面結構，再用確定性的規則修正可讀性與局部可演奏性**。

本專案現有分手器已具備按拍追蹤的 Blob 分界線、32 路束搜尋、手跨度／移動成本、旋律連續性、三手第二演奏者啟用區段及可選的材料分組。DuET 的 `readable/hands.py` 並不能直接替換這些既有機制。最有潛力的方向是：

1. **先做純程式實驗**：分手時區分新起音、仍按住的鍵與踏板造成的聲學延音，補足目前只看起音、忽略音長的限制。
2. **在雙手模式驗證模型先驗**：將 DuET 的逐音符 *staff* 判斷視為可拒絕的軟偏好，而不是實際左右手的真值；保留現有搜尋與演奏約束。
3. **借鑑局部修正與可追蹤輸出**：在初步分手後檢查不合理和弦，嘗試「最少換手、前後音高接近」的非破壞性修正，並記錄模型與規則的分歧。

**不能混淆的三個概念：聲部（voice）≠ 譜表（staff）≠ 實際演奏手（hand）。** DuET 學到的是樂譜標註，不保證還原實際手法。兩個譜表也不等於只可能由兩隻手演奏；它沒有本專案需要的三／四手及第二演奏者語義。

## 2. 對照的來源與範圍

- DuET 論文：https://arxiv.org/abs/2610.21467
- DuET 原始碼：https://github.com/wei-zeng98/DuET
- 模型入口：https://huggingface.co/Wayne42/DuET
- 本專案分手程式：[`src/hand_split_channels.py`](../src/hand_split_channels.py)
- 本專案設計契約：[`docs/spec/06-hand-split.md`](spec/06-hand-split.md)
- 本專案研究地圖：[`docs/research-landscape.md`](research-landscape.md)
- 本專案 Blob 事故報告：[`docs/INCIDENT-2026-10-26-BLOB-SEMANTIC-REGRESSION.md`](INCIDENT-2026-10-26-BLOB-SEMANTIC-REGRESSION.md)

本次閱讀了 DuET 的 `midi.py`、`features.py`、`codec.py`、`model.py`、`pipeline.py`、`assembly.py`、`engraving.py`、`export.py`、`readable/hands.py`、`readable/voices.py`、`readable/grid.py`、`readable/quantize.py`、`readable/pipeline.py`、`readable/README.md`，並對照本專案的分手程式及規格。**未下載模型、未運行推理、未以真實曲目 A/B 驗證；以下「可能改善」一律是待測假設。**

## 3. DuET 怎樣分配神經網路與程式碼

| 階段 | DuET 具體工作 | 實作位置 |
|---|---|---|
| MIDI 解析 | 將 note-on/off 配對，依 tempo map 換算秒數，讀取音高、力度、CC64 | `midi.py` |
| 生成特徵 | 每秒 50 幀、88 音高；分開表示 held、sustained、onset、onset velocity、踏板狀態等 | `features.py` |
| 神經網路推理 | Note/Frame 雙分支理解演奏；逐音預測小節、拍內位置、時值、staff、voice、拍號、調號等 | `model.py`、`codec.py` |
| 長曲處理 | 以 32 秒主窗口加 8 秒歷史窗口推理；一個輸入音符對應一個預測記錄 | `pipeline.py` |
| 組裝 | 合併窗口；對同一小節衝突的 metadata 按規則選擇；換算譜面絕對時間 | `assembly.py` |
| 可讀化 | 融合模型拍位與 frame 拍點，量化與處理 Swing、三連音／快速音群等 | `readable/grid.py`、`readable/quantize.py` |
| 譜表修正 | 對不合理和弦，嘗試移動最少音符的左右譜表切法，兼顧鄰近上下文 | `readable/hands.py` |
| 聲部和輸出 | 每個 staff 再區分聲部層；處理時值、調號、拼寫並輸出 MusicXML | `readable/voices.py`、`readable/pipeline.py`、`readable/musicxml.py` |

`codec.py` 中每顆音有 11 個預測欄位，包括 `staff`（兩類）與 `voice`（16 個類別）。模型沿用輸入音高，而不是重新辨識音高；輸出的原始 `notes.json`／`score.npz` 與可讀化的 `score.musicxml` 必須區分，因為可讀化步驟可能重新安排時間、譜表及刪減音符。`score.mid` 是量化後的試聽 MIDI；`export.py` 目前寫入固定 120 BPM，不應將其誤認為保留原始演奏時間的 MIDI。

## 4. 與本專案現有分手架構的關鍵差異

本專案 `hand_split_channels.py` 的主要路徑：

1. `collect_notes()` 解析原 MIDI；`group_notes()` 以 50 ms 起音窗口建立同時音組。
2. `blob_lines()` 每拍觀察前後音高空白帶，以 Viterbi 追蹤平滑分界。兩手時用於左右手；三／四手時是 **lower/middle musical mass 與 separated upper material 的分界**。
3. `search_splits()` 在每組內按音高枚舉連續切片，以跨度、音數、音域、移動、快速音群、Blob、可選連續性成本排序；保留 `BEAM_WIDTH = 32` 個狀態。
4. 三手另有至少 N 小節的第二演奏者 ON／OFF 區段；`split.grouping_bars = 1/2/4` 時可走 `material_groups()`／`search_grouped_splits()`，先建立材料責任偏好，再做切片搜索。
5. 寫入 hand 對應的 MIDI channel，保留原音符與時間；可彈性刪音屬另一步驟。

相比之下，DuET 的神經網路先預測每顆音屬於哪個譜表，`readable/hands.py` 主要做局部可演奏性補救，**不是**在多手、演奏者歸屬與樂句責任的空間中作整曲搜索。

本專案必須保留的順序與語義（低音 → 高音）：

- 兩手：`left → right`。
- 三手：`left → right → second_right`；Blob 下方是第一位演奏者兩手，上方才是第二位演奏者。
- 四手：`left → right → second_left → second_right`；Blob 下方仍是第一位演奏者，上方是第二位演奏者兩手。

**不可為了接 DuET 的二值 staff，偷偷將三／四手 Blob 重新定義成第一位演奏者自己的左右手分界。** 這正是既有 Blob semantic regression 事故應防止的類型。

## 5. 值得借鑑的具體實作

### 5.1 高優先：起音與「仍按住」分開建模（無需模型）

DuET 的 `features.RollRenderer` 產生不同特徵：

- `held`：依 note-on 到 note-off 的持鍵區間。
- `sustained`：在踏板作用下延長的發聲區間，並考慮同音再次起音。
- `onset_roll`：新起音位置；另有力度及踏板通道。

本專案 `blob_lines()` 目前只累積每拍附近的**新起音音高**，`hand_cost()` 的跨度與手指數也只看當下切片。`docs/spec/06-hand-split.md` 已承認「不考慮前面仍按著的音；不使用音長」。

可分成兩個不同用途：

- **結構訊號**：Blob 評分可適量參考跨拍持續的低音，避免低音沒有新起音時誤把低音團視為消失。
- **手部占用訊號**：一隻手若仍確實按住低音，新的遠距高音分配可能不合理；但如果只是踏板延音，不能當成手指仍被占用。

**重要限制**：本專案上游 ByteDance 轉譜 MIDI 的音尾可能包含踏板聲學延音，不保證是實際放鍵。故直接把 `end_ticks` 全部當作「指頭仍按住」會製造新錯誤。應先以來源標籤／踏板資訊建立可信度，只作軟性證據並比較有無改善。對只有混合延音時間、無可信放鍵事件的 MIDI，不能假裝可以可靠復原真實按鍵時長。

### 5.2 高優先：使用未來上下文做「最小換手」修正（純程式）

DuET `readable/hands.py::playability()` 在一個同時起音的音組不符合音數／跨度限制時，枚舉可能的左右譜表切法；若移動的音與**新譜表附近前後約兩拍的音**接近，且兩邊都能容納，優先選移動音符數最少的方案。

這提供了不同於本專案目前「累積前一手中心位置成本」的評估線索：讓候選切法也看**之後**的音高責任。但本專案既有 `material_groups()`、`melodic_links()` 已有部分跨時間證據，不宜直接再增加另一套重複的連續性懲罰；應先測試是否真的補足特定失敗案例。

DuET 在找不到合理分配時，還可能把和弦變琶音、將低音改為裝飾音或刪減重複音高。這屬**可讀譜面編修**，不適合直接併入本專案的「分手但不增刪音、不改時間」步驟。可先做非破壞性候選修正／診斷，保留原始結果以供對照。

### 5.3 實驗性：DuET staff 預測作為 Beam Search 的 soft prior

DuET 的模型會為每顆輸入音符預測兩個 staff 類別。若能從模型輸出取得兩類的概率 `P(staff | performance, context)`，可在雙手搜索成本中**另加可關閉的一項**：

```text
candidate_cost = existing_cost + λ × staff_prior_disagreement
staff_prior_disagreement = -log(max(P(predicted_staff_for_hand), ε))
```

上式只是待測設計示意，**不是 DuET 已提供的分手 API，也不是已調校的權重**。注意：

- `notes.json`／`score.npz` 提供原始預測的類別結果，不等於已提供逐音符概率。要取 logits／softmax，需另行修改 DuET 的推理介面，且應確認類別概率有無校準。
- 兩個模型 staff 表示**上／下譜表**，不是「右／左手」真值；交叉手、跨譜表寫法、同手跨譜表、轉譜模型訓練資料的記譜偏好都可能使先驗與實際手法不同。
- 先做兩手模式；三手時不能把 `second_right` 簡單映射成上譜表而宣稱模型能判斷是否需要第二演奏者。對三／四手可先純粹記錄分歧，**不進入決策成本**。
- 必須對齊的是**原輸入音符身份**。DuET `midi.py` 對重疊同音使用 FIFO 配對，本專案 `collect_notes()` 使用 LIFO；因此遇同音重疊時，可能連音尾配對都不同，不能只靠列表順序或音高／時間粗暴匹配。
- 不可把 `score.mid`（已量化、固定 120 BPM）當成對原 MIDI 的保真替代檔；建議使用原始 note-aligned 記錄作對照來源。

最值得觀察的不是「與 DuET 一致率提高多少」，而是**在有人工判斷或真實手別標註的失敗片段中，是否比單純規則搜索更好**。

### 5.4 中優先：將 staff 與 voice 分開，避免用分手硬解譜面問題

DuET `readable/voices.py` 在每個 staff 內再判斷主要及持續聲部，例如長低音保持、上方短音繼續移動，或三連音與二分格交錯。這說明「哪個聲部應該在同一譜表內如何排版」與「哪隻手演奏」應分成不同任務。

本專案輸出目標仍是 MIDI channel 手別，暫時不必為了這點引入 MusicXML 排版。若未來做樂譜生成，可在**確定 hand 之後**再處理 voice/layer；不要用聲部分離直接重寫 hand ownership。

### 5.5 工程細節：同音重疊配對、預測紀錄與可回溯性

- DuET `midi.py` 用 FIFO（`deque.popleft()`）配對同 pitch/channel 的 note-off，並警告重疊；本專案 `collect_notes()` 用 LIFO（`pop()`）。兩者都不能僅憑 MIDI 消息唯一還原含糊的同音重疊演奏。建議添加**資料診斷與個案對照**，勿直接把其中一個當作普遍正確的修復。
- DuET 保留原始預測 `notes.json`、`score.npz` 與 `review.json` 以區分模型判斷、可讀化修改和不確定區域。對本專案而言，可借鑑「分手候選／後處理修改／人工檢查」各自留痕，不混成同一個結果。
- 任何新的外部先驗或修正結果，應有可重現的模型版本、參數、原始音符 ID、改派原因及與 baseline 的差異，不覆蓋原 MIDI。

## 6. 不建議直接移植的部分

| DuET 元件 | 原因 |
|---|---|
| `readable/hands.py` 整套替換分手器 | 只有兩個 staff，且偏向不合理和弦的局部修正；缺少三／四手及演奏者責任模型 |
| 自動 thinning、grace、rolling 直接放入 hand split | 會改變音符數／時值／演奏方式，破壞本專案分手步驟的保真語義 |
| 把 staff 當作 hand 的監督標籤 | 記譜位置不等於演奏手 |
| 直接採用 DuET 試聽 `score.mid` 供分手 | 該檔經量化並固定 120 BPM，不是原始演奏的時間記錄 |
| 在三／四手模式讓二類先驗主導 Blob | 會破壞 upper separated material 對第二演奏者的結構語義 |
| 將模型推理結果當成品質證明 | 未經本專案實際曲目及人工標註驗證 |

## 7. 建議的最小實驗順序（未執行）

**實驗 A：持續音特徵的獨立 A/B（先不加入 AI）**

在不更改原搜尋 objective 與 Blob ownership 的前提下，先把「起音、note-off、踏板」整理為診斷表；找出新起音消失但持續音仍在的案例，再以可開關的軟性 feature 比較舊、新結果。測試須涵蓋：長低音、踩踏板的琶音、切分伴奏、單音旋律、快速重彈及音尾不可靠的 ByteDance 輸入。

**實驗 B：局部 lookahead 候選修正**

只對當前輸出中「超音數或超跨度」的起音組提出新切法；優先符合可演奏性、前後兩拍音高關聯及最少改派。不得刪音、移動音符、重定義 Blob，且先只產生差異報告，不覆蓋原分手結果。

**實驗 C：DuET 原始 staff 對照（兩手）**

在少數有人工可判定的片段上，將 DuET 原始 `notes.json` 按音符身份對齊，統計與現有分手不一致處；若確有補益，再嘗試額外 logits 軟性成本的可關閉分支。必須同時看誤分手、旋律被切碎、超跨度、交叉手與風格差異；**只看 DuET 一致率沒有驗證意義**。

**評估紀律**：

- 每次實驗保存 baseline 應用 revision、所有參數、模型版本／權重識別；不覆蓋原始資料。
- 測試資料、對照 MIDI、報告與臨時腳本只放在 `../piano-midi-cleanup-data/`；不提交主 repo。
- 先做合成回歸案例與真實曲目成對人工核對；程式結構測試通過 ≠ 音樂分手正確。
- 兩手與三／四手分開評估；三／四手須特別核對 Blob 上下歸屬、第二演奏者啟用區段和低到高手部順序。
- 若某次更改產生退化，先定位 root cause，不以繼續疊加 heuristic 掩蓋錯誤。

## 8. 授權與採用限制

DuET **程式碼**為 MIT；其發佈的 **模型權重**另採 **CC BY-NC 4.0**，不可把可用的程式碼授權誤認成模型權重也可自由商用。若未來需要商業產品內建模型，須另行確認授權或取得許可。純程式概念借鑑與實際複製程式碼亦須分開記錄來源與授權。

## 9. 最終判斷

**短期最務實的收穫，是用 DuET 的輸入表示與局部可演奏性修正思路補強本專案的規則式分手；中期才評估把模型的 staff 輸出當作可被演奏規則否決的先驗。**

DuET 提供的是「如何結合學習式音樂結構預測與確定性編修」的範例，而不是可以直接替換本專案分手器的完成方案。保護既有 Blob 語義與分手不增刪音的設計契約，比追求模型輸出一致更重要。

---
參考原始碼（截至 2026-11-05 查閱）：

- https://github.com/wei-zeng98/DuET/blob/main/src/duet/midi.py
- https://github.com/wei-zeng98/DuET/blob/main/src/duet/features.py
- https://github.com/wei-zeng98/DuET/blob/main/src/duet/codec.py
- https://github.com/wei-zeng98/DuET/blob/main/src/duet/pipeline.py
- https://github.com/wei-zeng98/DuET/blob/main/src/duet/readable/hands.py
- https://github.com/wei-zeng98/DuET/blob/main/src/duet/readable/voices.py
- https://github.com/wei-zeng98/DuET/blob/main/src/duet/readable/grid.py
- https://github.com/wei-zeng98/DuET/blob/main/src/duet/readable/quantize.py
- https://github.com/wei-zeng98/DuET/blob/main/src/duet/readable/pipeline.py
- https://github.com/wei-zeng98/DuET/blob/main/LICENSE-MODEL
