1.  **Historical Sync Logic**: The `run_sync` function has been completely revamped. It now reads the existing cache, determines the last synced date, and fetches only the missing days up to the present. This makes the sync process efficient.
2.  **Data Trimming**: After fetching and appending new data, the cache is trimmed to ensure it only stores the last `120` days, preventing it from growing indefinitely.
3.  **Enhanced AI Context**: The prompt-building functions (`build_user_prompt`, `build_chat_prompt`) have been updated to send the entire 120-day history to the AI. This allows for much richer, trend-based insights and more accurate chat responses.
4.  **Robustness**: The cache-checking logic in the `run_push` and `handle_chat` functions has been improved to correctly handle a missing or empty historical cache.
5.  **Code Organization**: The daily data fetching logic has been encapsulated into a new `_get_stats_for_day` helper function, improving clarity.

Here is the updated code:

