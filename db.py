import os, psycopg2

def get_db_connection():
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASS"),
        port=int(os.getenv("DB_PORT", "5432")),
        sslmode="prefer",
    )
    return conn

def archive_ticket_in_db(ticket_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE tickets
           SET archived = TRUE,
               status   = 'Canceled'
         WHERE id      = %s
        """,
        (ticket_id,)
    )
    updated = cur.rowcount > 0
    conn.commit()
    cur.close()
    conn.close()
    return updated


def get_user_email_for_ticket(ticket_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT submitted_by FROM tickets WHERE id = %s",
        (ticket_id,)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else None

