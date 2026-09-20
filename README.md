# AI News Agent

Project Python tổng hợp tin AI. Phase 4 hiện có RSS collector, arXiv collector và SQLite storage. Các bước LLM, báo cáo và gửi email nằm trong roadmap.

## Kiến trúc

```text
RSS ───────┐
arXiv ─────┼── collectors → Article → SQLite repository → NEW / DUPLICATE
Search ────┘
```

Collector chỉ chuyển dữ liệu Internet thành `Article`. CLI gửi Article vào repository; repository phụ trách lưu và chống trùng. `Article.source` là tên nguồn như `arXiv` hoặc tên RSS feed, còn `source_type` là `rss`/`arxiv`.

## Cấu trúc

| File hoặc thư mục | Vai trò |
| --- | --- |
| `src/ai_news_agent/models/article.py` | Article model dùng chung. |
| `src/ai_news_agent/collectors/rss.py` | Tải và chuyển RSS entries thành Article. |
| `src/ai_news_agent/collectors/arxiv.py` | Lấy metadata và abstract từ arXiv Atom API. |
| `src/ai_news_agent/collectors/search.py` | SearchProvider abstraction, Tavily provider và Article normalization. |
| `src/ai_news_agent/rss_sources.py` | RSS URLs mặc định. |
| `src/ai_news_agent/arxiv_config.py` | Category, keyword, khoảng ngày và giới hạn kết quả arXiv. |
| `src/ai_news_agent/search_config.py` | Query và giới hạn Web Search mặc định. |
| `src/ai_news_agent/storage/article_repository.py` | Tạo bảng SQLite, lưu, chống trùng và truy vấn Article. |
| `src/ai_news_agent/main.py` | CLI chạy RSS, arXiv hoặc cả hai rồi lưu SQLite. |
| `tests/` | Unit tests RSS, arXiv và SQLite; dùng dữ liệu mẫu và database tạm. |
| `.env.example` | Mẫu tên biến môi trường; CLI không tự tải file này. |
| `requirements.txt` | `feedparser` cho RSS và Atom; `python-dotenv` để nạp `.env`; `sqlite3` thuộc thư viện chuẩn. |

Các thư mục `processing/`, `workflow/`, `reporting/` và `delivery/` là skeleton cho các phase sau.

## Thiết lập và chạy

Yêu cầu Python 3.11 trở lên. Từ thư mục gốc project trong PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
python -m ai_news_agent.main --source rss
python -m ai_news_agent.main --source arxiv
python -m ai_news_agent.main --source search
python -m ai_news_agent.main --source all
```

Nếu `py` không tìm thấy Python, hãy cài Python 3.11+ trước. Database mặc định là `data/ai_news.db`; thư mục `data/` được tạo tự động. Có thể đổi đường dẫn bằng `--database PATH` hoặc biến môi trường `DATABASE_PATH`:

```powershell
python -m ai_news_agent.main --source all --database data/demo.db
```

Để dùng RSS URL riêng, lặp lại `--feed`:

```powershell
python -m ai_news_agent.main --source rss --feed https://example.com/feed.xml --feed https://another.example/rss
```

Để tùy chỉnh arXiv:

```powershell
python -m ai_news_agent.main --source arxiv --category cs.AI --category cs.LG --keyword "large language model" --max-results 10 --recent-days 7
```

Để tùy chỉnh Web Search, lặp lại `--query`:

```powershell
python -m ai_news_agent.main --source search --query "AI agents" --query "new AI model release" --search-max-results 5
```

Search dùng `TAVILY_API_KEY` từ environment variable hoặc file `.env` ở project root. `main.py` gọi `load_dotenv()` khi CLI khởi động; `.env.example` không được load. Đặt key trong PowerShell cho phiên hiện tại bằng `$env:TAVILY_API_KEY = "tvly-..."`, hoặc đặt `TAVILY_API_KEY=tvly-...` trong `.env`. Không đưa file `.env` vào repository. Nếu thiếu key, RSS và arXiv vẫn chạy; search báo lỗi cấu hình rõ ràng. `--source all` chạy cả ba nguồn và vẫn lưu các Article đã collect được.

Nếu chỉ truyền `--keyword`, CLI tìm trong 5 category mặc định. `--recent-days 0` tắt lọc ngày. Collector lấy metadata mới nhất từ từng category, lọc keyword trong title/abstract và ngày phát hành tại máy, gộp paper trùng category, rồi trả tối đa `max_results`. `ARXIV_SCAN_LIMIT` giới hạn số metadata quét mỗi category; paper nằm ngoài phạm vi quét có thể không xuất hiện. Collector đợi 3 giây giữa các yêu cầu arXiv API.

CLI in số Article thu được theo nguồn, tối đa 5 bài mẫu, rồi `Collected`, `New`, `Duplicates`, `Failed` và đường dẫn database. Lỗi của một RSS feed hoặc arXiv category không làm mất kết quả từ nguồn khác. CLI trả mã thoát 1 nếu có lỗi nguồn hoặc lưu trữ.

## SQLite và chống trùng

Bảng `articles` chứa `id`, `title`, `url`, `source`, `source_type`, `source_id`, `published_at`, `content`, `authors`, `collected_at`, `created_at`. `content` lưu `Article.summary`, tức mô tả RSS hoặc abstract arXiv gốc; chưa có tóm tắt do LLM tạo. `authors` lưu dưới dạng JSON. Ngày giờ lưu theo ISO 8601 UTC. `created_at` là lúc insert, còn `collected_at` là lúc tạo Article.

`url` có `UNIQUE` constraint trong SQLite. Repository trim URL, bỏ fragment, chuyển scheme/host thành chữ thường và bỏ cổng mặc định trước khi insert hoặc tìm kiếm; path và query được giữ nguyên. Nhờ đó lần chạy sau gặp cùng URL sẽ báo duplicate thay vì thêm hàng mới. `save_many` trả số mới/trùng/lỗi và chi tiết lỗi; Article lỗi không làm mất các Article hợp lệ trong batch. `list_recent` sắp theo ngày phát hành hoặc ngày insert nếu thiếu ngày phát hành.

Để reset database khi phát triển, đóng các process đang dùng nó rồi chạy:

```powershell
Remove-Item -LiteralPath data/ai_news.db
```

Lệnh trên xóa dữ liệu đã lưu; lần chạy CLI tiếp theo tạo database mới. Với database riêng, dùng đúng đường dẫn đã truyền qua `--database`.

## Roadmap

1. **Phase 1 — hoàn thành:** khung project và Article model.
2. **Phase 2 — hoàn thành:** RSS collector, CLI và tests.
3. **Phase 3 — hoàn thành:** semantics `source`/`source_type`, arXiv collector và tests.
4. **Phase 4 — hoàn thành:** SQLite repository, chống trùng URL và nối CLI với storage.
5. **Phase 5 — hoàn thành:** Tavily Web Search collector và provider abstraction.
6. **Phase 6:** phân loại, ranking và tóm tắt qua LLM API.
7. **Phase 7:** LangGraph orchestration và Daily Report HTML/PDF.
8. **Phase 8:** gửi email và chạy theo lịch bằng GitHub Actions.
