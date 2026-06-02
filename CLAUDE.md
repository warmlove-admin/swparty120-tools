# swparty120-tools — 我的班級工具總專案

## 對話開始時請先讀
進度與最近更動都在 Obsidian：`swparty120-tools/工作筆記.md`
Obsidian vault 路徑：`C:\Users\USER\iCloudDrive\iCloud~md~obsidian\swparty120\swparty120-tools\工作筆記.md`

## 工作模式
- **加新工具**：對 Claude 說「我想做一個 XXX 工具」→ Claude 會建 `tools/<工具名>/` 子資料夾、引導製作
- **結束工作**：對 Claude 說「**收工**」→ 自動 commit + push + 更新 Obsidian 工作筆記
- **接續工作**：對 Claude 說「**開工**」→ 讀工作筆記、報告 git 狀態、建議下一步

## 工作桌 + 三個家
- ☁️ iCloud 工作桌：`C:\Users\USER\iCloudDrive\Claude Code\swparty120-tools\`（自動跨電腦同步）
- 🐙 GitHub repo：`warmlove-admin/swparty120-tools`（公開，網頁的家）
- 📘 Obsidian 駕駛艙：`swparty120-tools/工作筆記.md`（想法的家）

## 工具清單
（之後加新工具時會自動更新）
- 空班媒合獎勵系統（`tools/空班媒合獎勵系統/`）

## 工作注意事項
- 學生資料一律去識別化（只用座號 + 班級代號）
- commit 訊息要寫清楚做了什麼 + 為什麼
- 收工前說「收工」讓 Claude 同步三方
- `.claude/` 永遠不要 commit（已加進 .gitignore）
