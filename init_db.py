import psycopg2
from db import get_db_connection

def create_users_table():
    print("🛠 Creating users table...")

    conn = get_db_connection()
    cursor = conn.cursor()

    # 🔥 Drop old users table
    cursor.execute("DROP TABLE IF EXISTS users")

    # ✅ Create new table with full structure
    cursor.execute("""
    CREATE TABLE users ( 
        id SERIAL PRIMARY KEY,
        first_name TEXT NOT NULL,
        last_name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        company TEXT NOT NULL,
        password TEXT NOT NULL,
        role TEXT DEFAULT 'user',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()
    cursor.close()
    conn.close()
    print("✅ Table created successfully!")

# Run it
create_users_table()
