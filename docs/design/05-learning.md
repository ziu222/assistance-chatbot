# Thiết kế bước 4: Learning: nhân viên dùng và sửa nháp, bot học qua bản phát hành tri thức

- Trạng thái: **L1–L3 đã implement** (L1: chế độ Learning, nháp trong ô soạn tin, `draft_uses`, `draft_exposure`; L2: `knowledge_releases`, `tenant_knowledge`, quay lại bản trước, `ContextBuilder` đọc snapshot; L3: route `learning`, `knowledge_candidates`, `LearningWorker` lọc PII + kiểm tra sự thật & chữ khách + trần chi phí); L4–L6 là thiết kế. Quyết định chính: [ADR-0038](../adr/0038-learning-through-reviewed-knowledge-releases.md).
- Ngày: 2026-11-25
- Liên quan:
  - [ADR-0030](../adr/0030-stateless-model-durable-knowledge.md) (tri thức bền trong Postgres, mục 5);
  - [02-agent-from-template.md](02-agent-from-template.md) (E17, §12.9 tín hiệu học, §13.4 chỗ thiếu tri thức);
  - [03-order-flow-and-unified-inbox.md](03-order-flow-and-unified-inbox.md) (O6 gợi ý chỉnh quy trình);
  - [04-shadow-comparison.md](04-shadow-comparison.md) (S5, `draft_exposure`).

## 1. Vấn đề

Sau bước 3, Atlas đo được nháp tốt hay kém theo từng ý định, nhưng chưa có đường nào để nháp **tốt lên**. Nhân viên vẫn gõ lại từ đầu những câu bot đã soạn gần đúng. Mỗi lần nhân viên sửa một con số hay thêm một chi tiết là một bài học bị bỏ phí. Ngược lại, nếu để bot "tự học" thẳng từ hội thoại thì sai một lần có thể nhân lên thành sai hàng loạt: chính sách cũ, câu nói đùa của nhân viên, hay chữ khách cố tình cài vào.

Bước 4 thêm chế độ **Learning** vào thang Manual → Shadow → Learning → Autopilot:

1. Nhân viên thấy nháp ngay trong inbox và chọn **dùng / sửa rồi gửi / bỏ**. Người vẫn là người gửi.
2. Mỗi lựa chọn thành một **bản ghi sửa**.
3. Hệ thống gom các bản ghi sửa thành **ứng viên tri thức** (FAQ mới, FAQ cần sửa, chỗ thiếu được lấp).
4. Chủ shop duyệt, ứng viên qua **eval**, rồi được phát hành thành một **bản phát hành tri thức** có phiên bản và quay lại được.

## 2. Mục tiêu và ngoài phạm vi

**Mục tiêu**

- Nhân viên dùng nháp với một cú bấm, sửa ngay trong ô soạn tin, hoặc bỏ qua. Không có gì tự gửi.
- Ghi lại nháp được dùng nguyên văn, được sửa (sửa gì: số tiền, sản phẩm, thêm thông tin) hay bị bỏ, để đo theo ý định và để học.
- Ứng viên tri thức **chỉ dựng từ chữ nhân viên đã gửi và dữ liệu shop**, không bao giờ từ chữ của khách (E17), có bằng chứng, đã kiểm tra sự thật và đã xóa dữ liệu cá nhân.
- **Không gì tự áp dụng:** mọi thay đổi tri thức đi qua chủ shop, qua eval, rồi phát hành có phiên bản. Một cú bấm là quay lại bản trước.
- Mỗi bài học thành một **ca hồi quy** trong eval, chạy lại khi đổi model, prompt hay template.
- Nối với bước 3 (S5): nháp nhân viên đã thấy được đo bằng mức sửa, không trộn với số đo "mù".

**Ngoài phạm vi (có chủ đích)**

- Tự gửi nháp (bước 5).
- Fine-tune, hay dùng bộ nhớ hoặc thread phía provider (ADR-0030).
- Học từ chữ của khách: kể cả "câu hỏi hay gặp" cũng được viết lại từ nhãn ý định và chủ đề, không chép lời khách.
- Tự sửa hành vi chuyển người. Bước 4 chỉ gợi ý; quy tắc chuyển người vẫn do template và chủ shop quyết.

## 3. Bất biến

1. **Chỉ người gửi tin cho khách.** Ở chế độ Learning, nháp là gợi ý trong ô soạn tin. Tin đi qua đúng API trả lời của nhân viên (`sender_type = 'human'`) kèm `draft_id`. Agent runtime vẫn không ghi `messages` (test architecture giữ nguyên).
2. **Ứng viên không chứa chữ của khách.** Bộ sinh ứng viên chỉ nhận chữ nhân viên đã gửi, nhãn ý định/chủ đề và dữ liệu shop. Có test kiểm tra.
3. **Không áp dụng tự động.** Ứng viên chỉ thành tri thức khi chủ shop duyệt **và** eval đạt.
4. **Tri thức có phiên bản.** `ContextBuilder` đọc bản phát hành đang hiệu lực, và `knowledge_hash` của run trỏ tới đúng bản đó. Quay lại = đổi con trỏ.
5. **Kiểm tra sự thật bằng code** cho mọi ứng viên, như trợ lý viết: số tiền, thời hạn, giờ, link trong ứng viên phải có trong chữ nhân viên làm bằng chứng. Lệch thì không duyệt được.
6. **Dữ liệu cá nhân không vào tri thức.** Số điện thoại, địa chỉ, mã đơn, tên người bị loại khỏi ứng viên bằng code trước khi chủ shop thấy. Ứng viên còn sót thì bị chặn duyệt.

## 4. Chế độ Learning trong inbox

### 4.1. Hiển thị nháp

- `agents.mode` thêm giá trị `learning`. Với hội thoại thuộc agent ở chế độ này, ô soạn tin hiện **"Gợi ý của bot"** với ba nút: **Dùng**, **Sửa** (đổ nháp vào ô soạn để sửa), **Bỏ**.
- Chỉ hiện nháp **mới nhất còn hợp lệ**: nháp ứng với tin khách cuối cùng (`based_on_sequence` ≥ tin khách mới nhất), có `grounded = true`, và chưa có tin nhân viên nào sau đó. Khách nhắn thêm thì nháp cũ biến mất, chờ nháp mới.
- Nháp `needs_human` vẫn hiện, kèm nhãn lý do (ví dụ "bot cho rằng cần nhân viên: chưa có chính sách đổi trả").
- Nháp có link hoặc số tiền: số tiền và link được tô sáng để nhân viên kiểm nhanh.

### 4.2. Gửi và ghi nhận

- `POST /conversations/{id}/messages` nhận thêm `draft_id` (tùy chọn). Server kiểm tra nháp thuộc đúng hội thoại và tenant, rồi ghi `draft_uses` cùng transaction với tin nhắn:
  - `action`: `as_is` (chữ gửi trùng nháp, so sau khi chuẩn hóa khoảng trắng), `edited`, hoặc `discarded` (nhân viên bấm Bỏ hoặc gửi tin khác mà không dùng nháp);
  - `edit_ratio`: tỉ lệ chỉnh sửa theo ký tự, từ 0 tới 1;
  - `diff_features` (code tính): số tiền thêm/bớt/đổi, sản phẩm đổi, link đổi, câu được thêm, câu bị xóa.
- Không chép chữ nhân viên vào `draft_uses`: bản ghi tham chiếu `message_id`; chữ nằm trong `messages`.
- `shadow_comparisons` của nháp này mang `draft_exposure = 'shown'`. Bước 3 tính riêng các cặp này bằng **mức sửa** thay vì so sánh độc lập (§7).

Đã implement (L1):

- `agents.mode` thêm `learning` (migration 0021).
  - Runner soạn nháp ở cả `shadow` lẫn `learning`. Riêng `learning` bỏ qua lấy mẫu, vì nhân viên cần nháp cho mọi tin.
  - Bước kiểm tra trước khi lưu chấp nhận cả hai chế độ.
- Module `learning`:
  - `domain/draft_use.py` (hàm thuần) tính `action`, `edit_ratio`, và `diff_features` gồm số tiền thêm/bớt, link đổi, câu thêm/bớt.
  - `infrastructure/drafts.py`: nháp hợp lệ, ghi lựa chọn, bỏ ngầm.
- API:
  - `GET /api/v1/conversations/{id}/draft` (ghi `agent_drafts.shown_at` lần đầu hiện);
  - `POST /api/v1/conversations/{id}/draft/{draft_id}/discard`;
  - API trả lời nhận `draft_id` và trả `draft_action`.
- Nhân viên tự gõ câu khác trong khi nháp **đã hiện** ⇒ ghi `discarded` kèm `message_id`. Nháp chưa từng hiện thì không ghi gì.
- `shadow_comparisons.draft_exposure = 'shown'` khi nháp đã hiện (`shown_at`).
- Tín hiệu học: `draft_used` / `draft_edited` / `draft_discarded`.
- **Khác thiết kế:**
  - `draft_uses` được ghi trong transaction ngay sau tin nhắn, không chung transaction, vì use case gửi tin nằm ở module `conversation`. Khóa chính `draft_id` làm việc ghi lại an toàn; lỗi ở đây chỉ mất một bản ghi học.
  - `diff_features` chưa so sản phẩm.
- Ô soạn tin trong admin (UI thử nghiệm) hiện "Gợi ý của bot" với Dùng / Sửa / Bỏ và tô số tiền cần kiểm. Đã thử trên trình duyệt với DB dev: Sửa → đổi giá → gửi thì ghi `edited`, `amounts_added = [320000]`, `amounts_removed = [350000]`.

### 4.3. Giá trị cho nhân viên đo được

Theo ý định:
- tỉ lệ dùng nguyên văn;
- tỉ lệ sửa nhẹ (`edit_ratio` < 0,2);
- tỉ lệ bỏ;
- thời gian từ lúc khách nhắn tới lúc nhân viên gửi, so giữa có nháp và không có nháp.

## 5. Bản phát hành tri thức

Hiện FAQ và tài liệu được sửa trực tiếp và có hiệu lực ngay. Bước 4 đổi thành:

- `knowledge_releases(tenant_id, number, snapshot jsonb, reason, created_by, eval_run_id)`, cùng con trỏ `tenant_knowledge`.
  - `snapshot` là toàn bộ FAQ/tài liệu đã duyệt tại thời điểm phát hành (id, nội dung, chủ đề). Shop nhỏ có vài chục mục, nên lưu cả ảnh chụp là đủ gọn.
  - `reason` là `manual_edit` hoặc `lesson`.
- `ContextBuilder` dựng khối `<knowledge>` từ `snapshot` của bản đang hiệu lực, không đọc thẳng bảng FAQ. `knowledge_hash` tính trên `snapshot`, nên bước 3 tự đếm lại từ đầu khi tri thức đổi.
- **Chủ shop sửa FAQ trong admin** ⇒ tạo bản phát hành mới ngay (`manual_edit`), không qua eval. Chủ shop chịu trách nhiệm chữ mình viết, và hành vi giống hiện nay. Có nút quay lại bản trước.
- **Bài học từ ứng viên** ⇒ bản nháp phát hành, chạy eval, đạt mới chuyển con trỏ (§6.4).
- Bảng FAQ/tài liệu vẫn là nơi soạn thảo; bản phát hành là thứ bot dùng. Bước 3 lấy `known_amounts` từ ảnh chụp đúng lúc soạn nháp, nên lỗi "nghi nhầm câu nhân viên khi giá vừa đổi" của S1 cũng hết.

Đã implement (L2):

- Migration 0022: tạo `knowledge_releases` (`snapshot jsonb`, `reason`, `created_by`, `eval_run_id`) và con trỏ `tenant_knowledge`.
- Domain thuần `knowledge/domain/releases.py`: `KnowledgeSnapshot`, `build_snapshot`, `render_knowledge` (sắp xếp cố định theo ID, bảo đảm cùng ảnh chụp ⇒ cùng `<knowledge>` và cùng `knowledge_hash`).
- `knowledge/infrastructure/releases.py`: `publish_snapshot`, `publish_from_tables`, `active_snapshot` (fallback đọc từ bảng nếu tenant chưa có bản phát hành nào), `list_releases`, `restore_release` (đổi con trỏ + ghi tín hiệu `release_published` chỉ gồm id/nhãn).
- `ContextBuilder` đọc từ `active_snapshot` và dựng `<knowledge>` / `knowledge_hash` qua `render_knowledge`.
- API: tạo/sửa/lưu trữ FAQ hoặc tài liệu tự phát hành bản mới (`manual_edit`) trong cùng transaction; thêm `GET /api/v1/knowledge/releases` và `POST /api/v1/knowledge/releases/{number}/restore`.
- Admin UI: tab **"Phiên bản"** trong `KnowledgePage` hiện danh sách bản phát hành và nút "Quay lại bản này".

## 6. Từ bản ghi sửa tới ứng viên

### 6.1. Loại ứng viên

| Loại | Khi nào | Nội dung |
|---|---|---|
| `faq_new` | nhiều lần nhân viên trả lời cùng một ý định/chủ đề mà tri thức chưa có (thường đi kèm `knowledge_gap` của 2.13) | câu hỏi viết lại từ chủ đề, câu trả lời từ chữ nhân viên |
| `faq_update` | nhân viên nhiều lần sửa cùng một con số hay chi tiết mâu thuẫn với một FAQ đang có | FAQ cũ → bản sửa đề xuất, kèm chỗ khác |
| `doc_section` | câu trả lời dài, lặp lại về chính sách (đổi trả, bảo hành) | một đoạn tài liệu |
| `brand_example` | nháp được dùng nguyên văn nhiều lần, giọng tốt | câu mẫu cho hồ sơ thương hiệu (chủ shop chọn) |
| `order_flow` | nhân viên thường hỏi thêm một trường khi xác nhận đơn (tín hiệu `order_confirmed`, thiết kế 03 O6) | đề xuất thêm trường vào quy trình đặt hàng |

### 6.2. Cách dựng

Một việc định kỳ (mỗi giờ) gom bản ghi sửa của 14 ngày theo tenant × ý định × chủ đề:

1. **Lọc bằng code:** chỉ lấy `edited` có `diff_features` đáng kể (đổi số tiền, sản phẩm hay thêm câu), hoặc `discarded` mà nhân viên tự trả lời có thông tin.
2. **Đủ bằng chứng:** ít nhất 2 lần nhân viên (có thể khác người) cùng sửa theo một hướng, ví dụ cùng một con số mới. Chỉ một lần thì chưa thành ứng viên.
3. **Route `learning`** (model rẻ, tách khỏi soạn nháp và giám khảo) viết ứng viên. Đầu vào chỉ gồm chữ nhân viên đã gửi, nhãn ý định/chủ đề, và FAQ liên quan đang có. Đầu ra có cấu trúc gồm loại, câu hỏi (nếu là FAQ), nội dung, và FAQ bị thay thế (nếu có).
4. **Kiểm tra bằng code:**
   - số tiền, thời hạn, link trong ứng viên phải có trong chữ nhân viên làm bằng chứng;
   - không có số điện thoại, địa chỉ, mã đơn, tên người;
   - không có chuỗi nào dài quá 8 từ trùng chữ của khách trong các hội thoại làm bằng chứng (chặn chữ khách lọt vào).
5. Ghi `knowledge_candidates` gồm loại, nội dung, bằng chứng (id các bản ghi sửa), kết quả kiểm tra, trạng thái `pending`.

Trần chi phí riêng `learning` theo tenant mỗi ngày, như trợ lý viết và giám khảo.

Đã implement (L3):

- Migration 0023: tạo bảng `knowledge_candidates` (loại, ý định, chủ đề, `fingerprint` chống đề xuất lặp trong 30 ngày, `proposal jsonb`, `replaces`, `evidence jsonb`, `checks jsonb`, `status`).
- Route `learning` trong `routes.toml` (model rẻ, tách riêng khỏi soạn nháp và giám khảo) và trần chi phí riêng `LEARNING` (`learning_daily_cap_micro`) trong `usage.py` / `settings.py`.
- Domain thuần `learning/domain/candidates.py`: `is_significant`, `has_enough_evidence` (≥ 2 lần sửa cùng hướng), `detect_pii` / `strip_pii`, `has_customer_overlap` (> 8 từ), `check_candidate` (số tiền/thời hạn/giờ/link có trong bằng chứng nhân viên), `fingerprint`, `user_turn` (chỉ chữ nhân viên + FAQ).
- Infrastructure tách biệt hai vai trò để khóa cứng E17:
  - `learning/infrastructure/candidates.py` (`build_candidates_once`, `LearningWorker` kèm Postgres advisory lock): chỉ truy vấn tin `human`, không bao giờ đọc tin `customer` (được khóa bởi `test_architecture.py`).
  - `learning/infrastructure/checks.py`: hậu kiểm bằng code sau khi LLM sinh xong (chỉ đọc tin `customer` để kiểm tra trùng chuỗi > 8 từ, không gọi LLM).
- Gắn vai trò `learning` vào `entrypoints/agent_worker.py` (`--roles`).

### 6.3. Chủ shop duyệt

- Màn hình **"Bot muốn học"** liệt kê ứng viên. Mỗi ứng viên có nội dung đề xuất, chỗ khác so với tri thức hiện tại, số lần nhân viên đã sửa, và link tới các hội thoại làm bằng chứng.
- Ba lựa chọn: **Duyệt**, **Sửa rồi duyệt**, **Bỏ** (kèm lý do tùy chọn, để không đề xuất lại cùng ý).
- Chỉ `owner` / `admin` được duyệt (cùng quyền `shadow.label` hoặc một quyền `knowledge.approve` mới).

### 6.4. Cổng eval rồi mới phát hành

Duyệt xong, hệ thống:

1. Dựng bản nháp phát hành = bản đang hiệu lực + thay đổi của ứng viên.
2. Tạo **ca hồi quy** cho bài học: một câu hỏi dựng từ chủ đề, kèm đáp án kỳ vọng (số tiền hay chi tiết phải có, điều cấm nói). Ca này lưu vào `eval_cases` của tenant.
3. Chạy eval nhỏ trên bản nháp phát hành: ca hồi quy mới, các ca hồi quy cũ của tenant, và một mẫu ca chuẩn theo ý định liên quan.
4. **Đạt:** ca mới đúng, không ca cũ nào từ đúng thành sai, và không có điều cấm ⇒ chuyển con trỏ, ứng viên thành `published`.
5. **Không đạt:** ứng viên thành `eval_failed`, kèm ca hỏng và câu bot trả lời, để chủ shop sửa lại.

Eval tốn tiền LLM (vài lời gọi mỗi bài học) và tính vào trần `learning`.

## 7. Nối với bước 3 (S5)

- Cặp có `draft_exposure = 'shown'` không được so độc lập nữa: nhân viên có thể đã chép nháp. Với các cặp này, bước 3 dùng `draft_uses`:
  - "gửi được" = `as_is`, hoặc `edited` với `edit_ratio` < 0,2 mà không đổi số tiền hay sản phẩm;
  - "sai" = `edited` có đổi số tiền hay sản phẩm.
- Bảng tổng hợp theo ý định tách hai nguồn: **mù** (Shadow) và **đã thấy** (Learning). Mức sẵn sàng cho bước 5 có thể dùng một trong hai, nhưng ghi rõ dùng nguồn nào.
- Ý định có tỉ lệ sai hoặc tỉ lệ bỏ cao được ưu tiên trong hàng ứng viên.

## 8. Dữ liệu và API (dự kiến)

**Bảng mới**

- `draft_uses`: draft_id (unique), tenant_id, conversation_id, message_id (nullable khi bỏ), action, edit_ratio, diff_features jsonb, user_id, created_at.
- `knowledge_releases`, `tenant_knowledge` (§5).
- `knowledge_candidates`: id, tenant_id, kind, intent, topic, proposal jsonb, replaces (id FAQ/tài liệu nếu có), evidence (id `draft_uses`), checks jsonb, status (`pending` / `approved` / `eval_failed` / `published` / `rejected`), reviewed_by, release_id.
- `eval_cases`: tenant_id, source (`lesson` / `manual`), candidate_id, question, expected jsonb, active.
- `agents.mode` thêm `learning`.

**API**

- `GET /api/v1/conversations/{id}/draft`: nháp hợp lệ để hiện trong ô soạn tin, chỉ khi agent ở chế độ `learning`.
- `POST /api/v1/conversations/{id}/messages`: thêm `draft_id`.
- `POST /api/v1/conversations/{id}/draft/{draft_id}/discard`.
- `GET /api/v1/knowledge/releases`, `POST /api/v1/knowledge/releases/{n}/restore`.
- `GET /api/v1/learning/candidates`, `POST /api/v1/learning/candidates/{id}/approve` (có thể kèm nội dung đã sửa), `POST /api/v1/learning/candidates/{id}/reject`.

**Tín hiệu học:** `draft_used`, `draft_edited`, `draft_discarded`, `candidate_approved`, `candidate_rejected`, `release_published`. Payload chỉ có id và nhãn.

## 9. Kiểm thử

- **Unit:**
  - `action` và `edit_ratio` (khoảng trắng, dấu câu);
  - `diff_features` (số tiền đổi, sản phẩm đổi, câu thêm);
  - lọc dữ liệu cá nhân;
  - kiểm tra "không chứa chữ khách" (chuỗi 8 từ);
  - quy tắc đủ bằng chứng (2 lần cùng hướng);
  - điều kiện đạt của cổng eval.
- **Integration:**
  - nháp chỉ hiện ở chế độ `learning` và còn hợp lệ;
  - gửi kèm `draft_id` ghi `draft_uses` cùng transaction;
  - `draft_id` của hội thoại hay tenant khác bị từ chối;
  - bản phát hành và quay lại;
  - `knowledge_hash` đổi theo bản phát hành;
  - ứng viên dựng bằng LLM giả;
  - duyệt rồi eval đạt thì phát hành, eval hỏng thì không;
  - tín hiệu học không có nội dung.
- **Architecture:** runtime vẫn không ghi `messages`; bộ sinh ứng viên không đọc tin `customer` (kiểm tra truy vấn).
- **Eval:** bộ shop giả lập thêm kịch bản "nhân viên sửa giá FAQ cũ hai lần". Kỳ vọng: sinh ứng viên `faq_update`, duyệt, eval đạt, và nháp sau đó dùng giá mới.

## 10. Trình tự

| Giai đoạn | Nội dung | Phụ thuộc |
|---|---|---|
| L1 | Chế độ `learning`; nháp trong ô soạn tin (UI thử nghiệm); gửi kèm `draft_id`; `draft_uses` + `diff_features`; `draft_exposure = 'shown'` | bước 3 S1 |
| L2 | Bản phát hành tri thức + con trỏ + quay lại; sửa FAQ tạo bản phát hành; `ContextBuilder` đọc ảnh chụp | — |
| L3 | Việc dựng ứng viên (route `learning`), lọc dữ liệu cá nhân, kiểm tra sự thật và chữ khách, trần chi phí | L1, L2 |
| L4 | Màn hình "Bot muốn học", duyệt/sửa/bỏ, ca hồi quy + cổng eval + phát hành | L3 |
| L5 | S5: đo theo mức sửa cho cặp đã thấy, tách nguồn trong tổng hợp; ưu tiên ứng viên theo ý định | L1, bước 3 S4 |
| L6 | Ứng viên `brand_example` và `order_flow` (thiết kế 03 O6) | L4 |

## 11. Rủi ro và câu hỏi mở

- **Nhân viên quen bấm "Dùng" mà không đọc (thiên vị tự động hóa).** Giảm bằng cách tô sáng số tiền và link, theo dõi tỉ lệ dùng nguyên văn theo nhân viên, và nối với bước 3: cặp "đã thấy" có mâu thuẫn số tiền vẫn bị bắt bằng code sau khi gửi.
- **Nhân viên sai cũng thành bằng chứng.** Cần ít nhất 2 lần cùng hướng, chủ shop duyệt, và eval trước khi phát hành. Ứng viên `faq_update` luôn hiện rõ chỗ khác với FAQ cũ.
- **Ứng viên lặp lại sau khi bị bỏ.** Lưu lý do bỏ và vân tay nội dung; không đề xuất lại cùng nội dung trong 30 ngày.
- **Chi phí:** dựng ứng viên và eval là việc nền có trần. Ước tính chính xác sau L3–L4.
- **Bản phát hành phình to** với shop có nhiều tài liệu. Ảnh chụp vẫn đủ ở quy mô hiện tại; nếu vượt khoảng 1 MB thì chuyển sang bảng phiên bản từng mục.
- **Câu hỏi mở:** cho nhân viên (không chỉ chủ shop) đề xuất "lưu câu này thành FAQ" bằng tay không? Đề xuất mặc định: có, nút nhỏ cạnh tin đã gửi, tạo ứng viên đi đúng đường duyệt và eval.

## 12. Tóm tắt một dòng

Ở chế độ Learning, nhân viên thấy nháp và dùng, sửa hay bỏ. Mỗi lựa chọn thành bản ghi sửa. Các sửa lặp lại thành ứng viên tri thức (chỉ từ chữ nhân viên, đã kiểm tra sự thật và xóa dữ liệu cá nhân). Chủ shop duyệt, ứng viên qua eval, rồi được phát hành thành bản tri thức có phiên bản và quay lại được. Không gì tự áp dụng, và người vẫn là người gửi.
