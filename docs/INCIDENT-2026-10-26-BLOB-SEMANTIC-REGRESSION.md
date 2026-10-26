# 三手分手 Blob 語義退化事故報告

**副標題：原因究明與再發防止**  
**專案：** `hibikiwtnb/piano-midi-cleanup`  
**事故範圍：** `hand_split_channels.py` 三手分配 / Blob 結構判斷  
**主要涉及提交：**
- `5249cd4` — `Split hands along a per-beat blob line and support a second right hand`
- `a2fe9d8` — `Fix multi-player hand topology and split costs`
- `fa9a724` — `Integrate three-hand playable reduction with Primo`（後續補償性設計，非事故起點）

---

## 1. 事故摘要

本事故的本質不是單純參數調整失敗，而是一次**演算法語義退化（semantic regression）**。

早期 `5249cd4` 已經實現了本專案真正需要的核心判斷：在 piano roll 上尋找低中音團與高音團之間持續存在的空白音域（blob line），並利用該結構約束三手分配。這個設計的音樂含義是：

> 當低中音區已形成一個完整音團，而上方又存在明顯分離的連續音團時，上方材料應由最高的額外手承擔。

`a2fe9d8` 在修正三手拓撲順序時，將原本錯誤的：

```text
left → second_right → right
```

修正為正確的：

```text
left → right → second_right
```

這一拓撲修正本身是正確且必要的。

但同一提交同時修改了 `blob_cost()` 的語義：原本 Blob 邏輯約束「最高那隻手 vs 其餘較低的手」，修改後卻只約束第一演奏者的 `left/right`，並**明確忽略 `second_right`**。

結果是：

> 原本用來判斷「上方是否形成獨立音團、是否需要額外演奏者」的核心結構訊號，被從第二演奏者決策中移除。

從此之後，`second_right` 主要只剩下 hand feasibility、movement/span/register 等一般分手成本與 `SECOND_HAND_NOTE_COST` 來控制。後續開發因此開始嘗試用 Primo activation、upkeep、playable reduction competition、beam search 等機制補償這個已經失去的結構判斷。

事故嚴重性評定：**High / 設計級回歸**。

---

## 2. 原始正確設計

### 2.1 `5249cd4` 的 Blob 算法

該版本對每一拍（並包含前後 context beat）收集音高，對每一條候選 pitch line 計算上下最近音符的距離。

空白帶越寬，分界線 score 越高；再用 Viterbi 讓分界線在時間上保持平滑：

```text
BLOB_CONTEXT_BEATS = 1
BLOB_MAX_GAP = 12
BLOB_LINE_MOVE_COST = 0.5
BLOB_SIDE_COST = 4.0
BLOB_CLEAR_GAP = 4
```

其本質是在 piano roll 上尋找：

```text
高音團：        ● ● ● ● ● ●
                  ↑
              明顯空白音域
                  ↓
低中音團：  ●●●  ●●●●  ●●●
```

### 2.2 原始 `blob_cost()`

早期三手 topology 雖然命名錯誤，但 Blob 的結構語義是完整的：

```python
wrong = sum(1 for pitch in slices[-1] if pitch < line)
wrong += sum(
    1 for pitches in slices[:-1]
    for pitch in pitches if pitch >= line
)
```

也就是：

- 最高的 slice 應位於 blob line 上方；
- 其餘較低的 slices 應位於 blob line 下方。

當時三手順序為：

```text
left → second_right → right
```

因此雖然 hand naming / player topology 是錯的，**最高音團應由最高 slice 承擔**這個結構原則仍然存在。

---

## 3. 事故變更

事故起點是：

```text
a2fe9d80e3ae04c15a876cd85fdc09d4ee4627e1
Fix multi-player hand topology and split costs
```

### 3.1 正確部分

該提交正確修正了三手與四手拓撲：

```python
HANDS = {
    2: ('left', 'right'),
    3: ('left', 'right', 'second_right'),
    4: ('left', 'right', 'second_left', 'second_right'),
}
```

這使得三手從低到高符合真實空間位置：

```text
left → right → second_right
```

### 3.2 錯誤部分

同一提交將 `blob_cost()` 改為：

```python
left, right = slices[:2]
wrong = sum(1 for pitch in right if pitch < line)
wrong += sum(1 for pitch in left if pitch >= line)
```

並加入了明確的設計註釋：

```text
Extra hands belong to player 2 and are intentionally ignored here;
their use is controlled by hand feasibility and SECOND_HAND_NOTE_COST
rather than forcing the upper blob into them.
```

這句註釋直接暴露了事故的**直接原因**：

> Blob 被重新解釋成「第一演奏者左手 / 右手的分界」，而不是「低中音主體 / 上方獨立音團的結構分界」。

此處不是單純 implementation bug，而是**對 Blob 演算法目的的錯誤重新定義**。

---

## 4. 原因究明

### 4.1 直接原因

在修正 hand topology 時，開發者認為：

- `left + right` 屬於 player 1；
- `second_right` 屬於 player 2；
- 因此 blob line 應只處理 player 1 的左右手；
- player 2 是否使用則交給 feasibility + `SECOND_HAND_NOTE_COST` 決定。

這個推論在程式結構上看似整齊，但在音樂語義上是錯的。

Blob 原本回答的不是：

> 「第一演奏者的左手和右手怎麼分？」

而是：

> 「整個音高空間中，是否存在一個與低中音主體明顯分離的上方音團？」

對三手模式而言，正確映射應是：

```text
blob line 下方：left + right
blob line 上方：second_right
```

而不是把 `second_right` 排除在 Blob 判斷之外。

### 4.2 根本原因（Root Cause）

**根本原因是「位置拓撲」與「演奏者歸屬」兩個概念被混為一談。**

原始程式使用 `slices[-1]` 表達「最高音域的手」。在 topology 修正後，最高音域從 `right` 變為 `second_right`。

正確處理方式應該是：

> 保留「最高 slice 對應 upper blob」的語義，只修正 hand 名稱與 performer mapping。

實際處理方式卻是：

> 因為 `second_right` 屬於另一位演奏者，所以把 Blob 改成只作用於 `left/right`。

因此一次「拓撲修正」被錯誤擴張成了「cost function 語義重寫」。

---

## 5. Five Whys

### Why 1：為什麼第二演奏者不再跟隨上方獨立音團？

因為 `blob_cost()` 在 `a2fe9d8` 後完全忽略 `second_right`。

### Why 2：為什麼會忽略 `second_right`？

因為 Blob 被重新定義為第一演奏者 `left/right` 的分界。

### Why 3：為什麼會重新定義？

因為 topology 修正時把「player ownership」誤當成 Blob 的語義邊界，沒有保留原始「低音團 vs 高音團」的結構目的。

### Why 4：為什麼這種語義改動能直接進入主線？

因為該提交同時包含：

- hand topology 修正；
- Blob cost 語義修改；
- register / second-player cost mapping 修改。

沒有將「必要的 topology 修正」與「演算法行為改變」分成獨立變更，也沒有要求對後者給出獨立設計依據與 A/B 行為驗證。

### Why 5：為什麼測試沒有抓到？

因為測試主要驗證：

- channel / hand order；
- cost 計算；
- pipeline 是否能執行；

而沒有驗證最重要的音樂行為 invariant：

> 當低中音形成完整音團、上方有明顯且持續分離的高音音團時，三手模式應優先把上方音團交給 `second_right`。

---

## 6. 事故影響

### 6.1 直接影響

`second_right` 不再由高低音團的結構關係主導，而變成一般 cost optimization 的額外自由度。

因此演算法開始回答錯誤問題：

> 「把這顆音交給第三手能不能降低 span / movement / register cost？」

而不是原本應回答的：

> 「這裡是否存在明顯分離、值得由第二演奏者承擔的上方音團？」

### 6.2 後續影響

由於失去了原本最重要的結構訊號，後續開發逐步加入：

- Primo activation cost；
- minimum activation bars；
- Primo upkeep；
- integrated playable reduction competition；
- phrase evidence；
- fixed 4-bar experiments；
- mode-separated beam；
- PianoPlayer difficulty experiment。

這些研究本身提供了有價值的資訊，但其中相當一部分是在嘗試用更複雜的機制補償 Blob 語義退化後產生的症狀。

### 6.3 搜尋實驗揭露的結果

後續 mode-separated beam 實驗證明：

- 原 beam 的較稀疏 Primo 結果部分是 pruning artifact；
- 正確搜尋目前 objective 時，演算法傾向讓 Primo 覆蓋大部分曲目；
- 這進一步說明一般演奏 cost 並不能代替「獨立上方音團」的結構判斷。

---

## 7. 為什麼事故沒有立即被發現

1. **Topology 修正表面上是正確的。**  
   `left → right → second_right` 本身沒有問題，因此整個提交容易被視為單純 bug fix。

2. **修改後仍然能產生合理-looking MIDI。**  
   `SECOND_HAND_NOTE_COST`、span、movement 等 cost 仍會約束結果，沒有立即 crash 或產生完全荒謬的輸出。

3. **缺少語義級 golden test。**  
   沒有一個小型 synthetic MIDI 明確描述「下方完整音團 + 上方持續旋律」並斷言其應分配給第三手。

4. **缺少行為指標回歸。**  
   hand-split 修改後沒有自動比較：
   - `second_right` 音數；
   - third-hand active bars；
   - blob line 上下錯側音數；
   - 上方音團被 `second_right` 接管的比例。

---

## 8. 修正原則

本事故不應以「回到錯誤 topology」來修復。

應保留：

```text
left → right → second_right
```

但恢復 Blob 的原始結構語義。

三手時：

```text
blob line 下方：left + right
blob line 上方：second_right
```

因此 Blob cost 應懲罰：

- `second_right` 取得 line 以下的音；
- `left` 或 `right` 取得 line 以上的音。

兩手時仍保持：

```text
blob line 下方：left
blob line 上方：right
```

四手模式需要另行定義，不能在本次事故修復中憑直覺擴張。

---

## 9. 再發防止措施

### P0：建立不可破壞的設計 invariant

在 spec 與程式註釋中明確寫入：

> Blob line 是音高空間中的「lower/middle musical mass vs separated upper material」結構分界，不是單純的 player-1 left/right boundary。

三手 topology 改名、channel 改動、performer mapping 改動都不得改變這個 invariant，除非另有獨立設計提案與驗證。

### P0：新增 synthetic regression test

新增最小 MIDI fixture：

```text
連續數拍：
低中音：C2/G2/C3/E3/G3 等完整 accompaniment cluster
高音：   C6-D6-E6-F6... 連續 melody
中間保持明顯 pitch gap
```

三手模式必須驗證：

- 高音團主要進入 `second_right`；
- `left/right` 主要留在 blob line 下方；
- topology 為 `left → right → second_right`。

再增加反例：只有一兩顆偶發高音，不應因測試規則被錯誤視為長期 upper structure。

### P0：禁止在「修 topology」提交中暗改 objective semantics

工程規則：

> 結構/命名/channel/topology bug fix 與 cost/objective/heuristic 行為修改必須分開提交。

如果 topology 修正要求修改 cost function，必須在提交訊息中明確列出：

- 舊語義；
- 新語義；
- 為什麼必須改；
- 預期行為差異；
- regression test。

### P1：避免依賴 slice index 表達音樂角色

禁止使用缺乏語義的：

```python
slices[-1]
slices[:2]
slices[2:]
```

直接承擔複雜 performer 意義。

應先建立明確角色映射，例如：

```text
player1_lower_hands
upper_extra_hand
```

或用 hand name 查找，而不是靠位置推導 performer 語義。

### P1：增加 hand-split 行為報告

每次修改分手算法後至少輸出：

- 每手 note count；
- 每手 active bars；
- `second_right` active segments；
- blob wrong-side note count；
- 與 baseline 的差值。

若 `second_right` 使用量發生大幅變化，必須人工確認，而不能只依賴單元測試通過。

### P1：建立 Golden MIDI A/B 回歸

選數個代表性片段：

1. 明顯上下兩團；
2. 普通兩手 accompaniment + melody；
3. 偶發超高音；
4. 大跨度但沒有獨立上方音團；
5. 快速 arpeggio。

每次 hand-split objective 變更必須與 golden baseline 比較。

### P2：保留算法設計歷史

對重要 heuristic 不只記錄「參數是多少」，還要記錄：

- 它試圖回答什麼音樂問題；
- 它不應回答什麼問題；
- 哪些 invariants 不得因重構而改變。

避免未來開發者看到 `blob_cost()` 只把它理解成普通 cost term，再次重新解釋其用途。

---

## 10. 責任與流程定性

本事故不應定性為某一個人的單點失誤，而應定性為：

> **缺乏語義 invariant、變更範圍控制與音樂行為 regression test 的工程流程事故。**

`a2fe9d8` 的錯誤修改有清楚的邏輯動機，並非隨機 typo；正因如此才更危險：它在程式結構上自洽，但改變了原算法真正要解決的音樂問題。

最需要防止的不是「以後不要寫錯這一行」，而是：

> **任何看似合理的重構或 topology 修正，都不能在沒有明確設計證明與行為回歸的情況下重新定義核心 heuristic 的語義。**

---

## 11. 最終結論

事故起點已確認為 `a2fe9d8`。

該提交中：

- 修正 `left → right → second_right`：**正確**；
- 將 Blob 限制為 `left/right` 並忽略 `second_right`：**錯誤且造成語義退化**。

真正應該做的修改是：

```text
保留正確 topology
+
恢復 Blob 對 upper separated cluster 的結構約束
```

即：

```text
2 hands:
    lower blob → left
    upper blob → right

3 hands:
    lower/middle blob → left + right
    upper separated blob → second_right
```

此次事故的最大教訓是：

> **演算法的「音樂語義」必須被視為 API contract 一樣保護。程式結構可以重構，hand name 可以修正，但不能在沒有證據的情況下把一個「結構判斷器」悄悄改寫成另一種成本函數。**
