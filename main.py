from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import bcrypt
from db import get_db_connection
from datetime import datetime

app = FastAPI()

# ✅ CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Match your frontend
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ Models
class RegisterRequest(BaseModel):
    first_name: str
    last_name: str
    email: str
    company: str
    password: str

class LoginRequest(BaseModel):
    email: str
    password: str

# Input model for creating a ticket
class TicketIn(BaseModel):
    title: str
    description: str
    submitted_by: str
    status: str = "Open"
    priority: str = "Medium"
    screenshot: str | None = None

# Output model for returning tickets
class TicketOut(TicketIn):
    id: int
    created_at: datetime
    updated_at: datetime
    archived: bool


# ✅ /register route
@app.post("/register")
def register_user(user: RegisterRequest):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM users WHERE email = %s", (user.email,))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="User already exists")

        hashed_pw = bcrypt.hashpw(user.password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

        cursor.execute(
            """
            INSERT INTO users (first_name, last_name, email, company, password)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (user.first_name, user.last_name, user.email, user.company, hashed_pw),
        )

        conn.commit()
    finally:
        cursor.close()
        conn.close()

    return {"message": "User registered successfully"}


# ✅ /login route
@app.post("/login")
def login_user(login: LoginRequest):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT password, role FROM users WHERE email = %s", (login.email,))
    user = cursor.fetchone()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    hashed_pw, role = user

    if not bcrypt.checkpw(login.password.encode("utf-8"), hashed_pw.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Incorrect password")

    cursor.close()
    conn.close()

    return {
        "message": "Login successful",
        "role": role
    }

from typing import Optional
from fastapi import Query

@app.get("/tickets", response_model=list[TicketOut])
def list_tickets(user_email: Optional[str] = Query(
    None,
    description="If provided, return only tickets submitted by this email"
)):
    conn = get_db_connection()
    cur  = conn.cursor()

    # Base query
    sql    = """
      SELECT id, title, description, submitted_by, status, priority,
             created_at, updated_at, archived, screenshot
      FROM tickets
      WHERE archived = FALSE
    """
    params = []

    # If a normal user passed user_email, add the filter
    if user_email:
        sql += " AND submitted_by = %s"
        params.append(user_email)

    sql += " ORDER BY created_at DESC"

    cur.execute(sql, params)
    cols = [c[0] for c in cur.description]
    rows = cur.fetchall()
    cur.close()
    conn.close()

    return [TicketOut(**dict(zip(cols, row))) for row in rows]





@app.post("/tickets", response_model=TicketOut)
def create_ticket(ticket: TicketIn):
    now = datetime.utcnow()
    conn = get_db_connection()
    cur  = conn.cursor()
    cur.execute("""
        INSERT INTO tickets
          (title, description, submitted_by, status, priority, created_at, updated_at, screenshot)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id
    """, (
        ticket.title,
        ticket.description,
        ticket.submitted_by,
        ticket.status,
        ticket.priority,
        now,
        now,
        ticket.screenshot
    ))
    ticket_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()

    return TicketOut(
        id=ticket_id,
        title=ticket.title,
        description=ticket.description,
        submitted_by=ticket.submitted_by,
        status=ticket.status,
        priority=ticket.priority,
        screenshot=ticket.screenshot,
        created_at=now,
        updated_at=now,
        archived=False
    )
# --- NEW PATCH ROUTE ----------------------------------------------
from typing import Optional
from pydantic import BaseModel

class TicketUpdate(BaseModel):
    title:        Optional[str] = None
    description:  Optional[str] = None
    status:       Optional[str] = None
    priority:     Optional[str] = None
    updated_at:   Optional[datetime] = None

@app.patch("/tickets/{ticket_id}", response_model=TicketOut)
def patch_ticket(ticket_id: int, changes: TicketUpdate):
    conn = get_db_connection()
    cur  = conn.cursor()

    cur.execute("SELECT * FROM tickets WHERE id = %s", (ticket_id,))
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        raise HTTPException(status_code=404, detail="Ticket not found")

    # Pull only the fields sent from the front‑end
    update_data = {k: v for k, v in changes.dict().items() if v is not None}
    if update_data:      # build SET … query dynamically
        set_clause  = ", ".join(f"{k} = %s" for k in update_data.keys())
        values      = list(update_data.values()) + [ticket_id]
        cur.execute(f"UPDATE tickets SET {set_clause} WHERE id = %s", values)
        conn.commit()

    cur.execute("SELECT * FROM tickets WHERE id = %s", (ticket_id,))
    updated_row = cur.fetchone()
    cols = [c[0] for c in cur.description]

    cur.close()
    conn.close()
    return TicketOut(**dict(zip(cols, updated_row)))
# -------------------------------------------------------------------
