## Summary

<!-- 說明解決的問題、觸發條件與變更後行為。 -->

## Related issue

<!-- Closes #123；沒有相關 issue 時請填 N/A。 -->

## Type of change

- [ ] Bug fix
- [ ] Feature
- [ ] Refactoring / performance
- [ ] Tests / CI / documentation
- [ ] Breaking change / database migration

## Implementation details

<!-- 說明重要設計、相容性與取捨，避免重述 diff。 -->

## Test evidence

<!-- 列出實際執行的指令、環境與結果；未執行項目請說明原因。 -->

| Check | Environment | Result |
| --- | --- | --- |
| `hatch run lint` | | |
| `hatch run types:check` | | |
| `hatch run test-cov` | | |
| Local PostgreSQL / Alembic | | |
| Manual real-person Pose validation, if applicable | | |

## Coverage changes

<!-- 前後涵蓋率、具體失敗分支與回歸情境；百分比不等於動作辨識準確率。 -->

## Breaking changes

<!-- API／WebSocket／資料格式的影響與遷移方式，或 N/A。 -->

## Migration impact

<!-- revision、回填、鎖定／停機風險、rollback 前提與可能資料遺失，或 N/A。 -->

## Security review

<!-- 描述身分驗證、授權、資料隔離與紀錄遮罩的檢查證據，或說明不適用。 -->

## Reviewer notes

<!-- 建議審查順序、已知限制與仍待確認項目。 -->

## Definition of Done

- [ ] 品質閘門通過，或已明列尚未通過的項目與原因。
- [ ] 新測試驗證可觀察行為與失敗情境，沒有弱化既有斷言或 70% 涵蓋率門檻。
- [ ] 文件與測試證據符合最終實作；未提交機密或真實個人生理資料。
- [ ] 已區分自動測試、CI 結果、部署驗收與真人動作辨識準確率。

Backend checklist：勾選已檢查的項目；不適用者請在上方說明。

- [ ] FastAPI API contract：路徑、schema、狀態碼與相容性。
- [ ] Better Auth JWT verification：簽章、issuer、audience、expiry 與 subject。
- [ ] WebSocket authentication and Origin：subprotocol、allowlist、大小／速率限制與連線清理。
- [ ] Pose Visibility Guard：ACQUIRING／ACTIVE／PAUSED／LOST、濾波器與重設生命週期。
- [ ] FSM correctness：完整循環、未完成循環、中斷與誤判回歸；未擅改動作門檻。
- [ ] PK lifecycle：加入、ready、重複事件、計分驗證、斷線與房間清理。
- [ ] Database migration and rollback risks：SQLite／PostgreSQL 差異與不可逆資料風險。
- [ ] RLS and user data isolation：資料所有權來自已驗證 JWT，跨使用者存取遭拒。
- [ ] Nullable body information：omitted／null／unknown、量測歷史與熱量可用性。
- [ ] No physiological fake defaults：沒有捏造體重、體脂、BMR 或未知熱量。
