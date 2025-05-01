import psycopg2

def get_db_connection():
    conn = psycopg2.connect(
        host="35.209.247.212",
        database="dbsz72db6xwawm",
        user="uekqdzgiefoxn",
        password="Metro2024!",
        port=5432,
        sslmode="prefer"  # 🔁 Change 'require' to 'prefer'
    )
    return conn
