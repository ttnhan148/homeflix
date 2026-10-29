import os
import json
import sqlite3
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_FILE = os.path.join(BASE_DIR, "saved_movies.json")
DB_FILE = os.path.join(BASE_DIR, "homeflix.db")

def migrate():
    if not os.path.exists(JSON_FILE):
        print(f"Không tìm thấy file JSON tại: {JSON_FILE}")
        return

    print("Khởi tạo cơ sở dữ liệu SQLite...")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS saved_movies (
            slug TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            poster_url TEXT,
            year TEXT,
            episode_current TEXT,
            total_episodes INTEGER,
            last_watched_episode TEXT,
            last_watched_url TEXT,
            episodes TEXT,
            episode_states TEXT,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()

    print(f"Đọc dữ liệu từ {JSON_FILE}...")
    try:
        with open(JSON_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"Lỗi khi đọc file JSON: {e}")
        conn.close()
        return

    print(f"Bắt đầu di chuyển {len(data)} bộ phim vào SQLite...")
    count = 0
    now_str = datetime.now().isoformat()
    for slug, movie in data.items():
        name = movie.get("name", "")
        poster_url = movie.get("poster_url", "")
        year = str(movie.get("year", ""))
        episode_current = movie.get("episode_current", "")
        total_episodes = movie.get("total_episodes")
        last_watched_episode = movie.get("last_watched_episode", "")
        last_watched_url = movie.get("last_watched_url", "")
        
        episodes_list = movie.get("episodes", [])
        episodes_json = json.dumps(episodes_list, ensure_ascii=False)
        
        episode_states_dict = movie.get("episode_states", {})
        episode_states_json = json.dumps(episode_states_dict, ensure_ascii=False)

        cursor.execute("""
            INSERT OR REPLACE INTO saved_movies (
                slug, name, poster_url, year, episode_current, total_episodes,
                last_watched_episode, last_watched_url, episodes, episode_states, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            slug, name, poster_url, year, episode_current, total_episodes,
            last_watched_episode, last_watched_url, episodes_json, episode_states_json, now_str
        ))
        count += 1

    conn.commit()
    conn.close()
    print(f"Đã di chuyển thành công {count} bộ phim.")

    # Đổi tên file để backup
    bak_file = JSON_FILE + ".bak"
    try:
        os.rename(JSON_FILE, bak_file)
        print(f"Đã đổi tên {JSON_FILE} thành {bak_file}")
    except Exception as e:
        print(f"Lỗi khi đổi tên file backup: {e}")

if __name__ == "__main__":
    migrate()
