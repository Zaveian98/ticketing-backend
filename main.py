from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import bcrypt
from ticketing_backend.db import get_db_connection
from datetime import datetime, timezone
from typing import Optional, List
import logging
from fastapi import Form, File, UploadFile


# configure root logger at DEBUG (you can bump to INFO later)
logging.basicConfig(level=logging.DEBUG)
# grab Uvicorn’s “error” logger so messages show up in its console
logger = logging.getLogger("uvicorn.error")




app = FastAPI(debug=True)

# ← CORS MUST go here, before any @app.on_event or @app.get/@app.post
app.add_middleware(
  CORSMiddleware,
  allow_origins=["*"],
  allow_methods=["*"],
  allow_headers=["*"],
  allow_credentials=False,
)



@app.on_event("startup")
def on_startup():
    ...

    print("🚀 App has started—listing routes:")
    for route in app.router.routes:
        print(" •", getattr(route, "path", None))



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
    assigned_to: str | None = None
    created_at: datetime
    updated_at: datetime
    archived: bool
    
class TaskIn(BaseModel):
    text: str
    completed: bool = False
    priority: str = "Low"
    assigned_to: Optional[str] = None
    screenshot_url: Optional[str] = None
    user_email: str  
class TaskOut(TaskIn):
    id: int
    user_email: str
    created_at: datetime
    updated_at: datetime
class TaskUpdate(BaseModel):
    text: Optional[str] = None
    completed: Optional[bool] = None
    priority: Optional[str] = None
    assigned_to: Optional[str] = None
    screenshot_url: Optional[str] = None


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


@app.get("/tickets", response_model=list[TicketOut])
def list_tickets(
    user_email: Optional[str] = Query(
        None,
        description="If provided, return only tickets submitted by this email"
    ),
    archived: bool = Query(
        False,
        description="If true, return archived tickets instead of active ones"
    ),
):
    conn = get_db_connection()
    cur  = conn.cursor()

    sql = """
  SELECT id, title, description, submitted_by, status, priority,
       assigned_to,
       created_at, updated_at, archived, screenshot
FROM tickets
WHERE archived = %s

"""
    params = [archived]



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


@app.post("/tasks", response_model=TaskOut)
async def create_task(
    text: str                   = Form(...),
    completed: bool             = Form(False),
    priority: str               = Form("Low"),
    assigned_to: Optional[str]  = Form(None),
    user_email: str             = Form(...),
    screenshot: UploadFile      = File(None),
):
    now = datetime.now(timezone.utc)

    # Save uploaded file (if any) and build a URL
    screenshot_url = None
    if screenshot:
        dest = f"./static/uploads/{screenshot.filename}"
        with open(dest, "wb") as f:
            f.write(await screenshot.read())
        screenshot_url = f"/static/uploads/{screenshot.filename}"

    # ← Normalize empty string to None
    if not assigned_to:
        assigned_to = None

    # Insert into DB
    conn = get_db_connection()
    cur  = conn.cursor()
    cur.execute("""
        INSERT INTO tasks
          (text, completed, priority, assigned_to, screenshot_url, user_email, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (
        text,
        completed,
        priority,
        assigned_to,
        screenshot_url,
        user_email,
        now,
        now
    ))
    task_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()

    return TaskOut(
        id=task_id,
        text=text,
        completed=completed,
        priority=priority,
        assigned_to=assigned_to,
        screenshot_url=screenshot_url,
        user_email=user_email,
        created_at=now,
        updated_at=now
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
    assigned_to: Optional[str] = None
    archived:     Optional[bool]     = None 


class UserOut(BaseModel):
    first_name: str
    last_name:  str
    email:      str
    role:       str
    company:    str



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
    if update_data:
        set_clause = ", ".join(f"{k} = %s" for k in update_data.keys())
        now        = datetime.now(timezone.utc)
    # Build params in the exact same order as your placeholders:
        params     = list(update_data.values()) + [now, ticket_id]

    cur.execute(
      f"UPDATE tickets SET {set_clause}, updated_at = %s WHERE id = %s",
      params
    )

    
    
    conn.commit()


    cur.execute("SELECT * FROM tickets WHERE id = %s", (ticket_id,))
    updated_row = cur.fetchone()
    cols = [c[0] for c in cur.description]

    cur.close()
    conn.close()
    return TicketOut(**dict(zip(cols, updated_row)))

@app.get("/users", response_model=List[UserOut])
def list_users(role: Optional[str] = Query(None, description="Filter users by role, e.g. ?role=admin")):
    """
    List users, optionally filtering by role.
    """
    conn = get_db_connection()
    cur = conn.cursor()

    sql = """
        SELECT first_name, last_name, email, role, company
        FROM users
    """
    params: list = []
    if role:
        sql += " WHERE role = %s"
        params.append(role)
    sql += " ORDER BY last_name"

    cur.execute(sql, params)
    cols = [c[0] for c in cur.description]
    rows = cur.fetchall()
    cur.close()
    conn.close()

    return [UserOut(**dict(zip(cols, row))) for row in rows]

# ─── Task Endpoints ───────────────────────────────────────────────────────────

@app.get("/tasks", response_model=list[TaskOut])
def list_tasks(
    user_email: Optional[str] = Query(
        None, description="If provided, return only tasks created by this email"
    )
):
    print("🚦 GOT /tasks – user_email:", user_email)
    
    conn = get_db_connection()
    cur  = conn.cursor()
    sql = """
        SELECT id, text, completed, priority, assigned_to,
               screenshot_url, user_email, created_at, updated_at
        FROM tasks
        {}
        ORDER BY created_at DESC
    """
    params = []
    if user_email:
        sql = sql.format("WHERE user_email = %s")
        params.append(user_email)
    else:
        sql = sql.format("")

    cur.execute(sql, params)
    cols = [c[0] for c in cur.description]
    rows = cur.fetchall()
    cur.close()
    conn.close()

    return [TaskOut(**dict(zip(cols, row))) for row in rows]




# ─── TaskUpdate + TaskOut models above ──────────────────────

import traceback
from fastapi import Request

@app.patch("/tasks/{task_id}", response_model=TaskOut)
async def update_task(task_id: int, changes: TaskUpdate, request: Request):
    #try:
        body = await request.json()
        logger.debug(f"PATCH payload: {body!r}")
        conn = get_db_connection()
        cur  = conn.cursor()

        # 1️⃣ Existence check
        cur.execute("SELECT id FROM tasks WHERE id = %s", (task_id,))
        if not cur.fetchone():
            cur.close()
            conn.close()
            raise HTTPException(status_code=404, detail="Task not found")

        # 2️⃣ Build dynamic SET clause
        update_data = {k: v for k, v in changes.dict().items() if v is not None}
        if update_data:
            set_clause = ", ".join(f"{k} = %s" for k in update_data)
            now        = datetime.now(timezone.utc)
            params     = list(update_data.values()) + [now, task_id]

            logger.debug(">> SQL = UPDATE tasks SET %s, updated_at = %%s WHERE id = %%s", set_clause)
            logger.debug(">> Params = %r", params)

            cur.execute(
                f"UPDATE tasks SET {set_clause}, updated_at = %s WHERE id = %s",
                params
            )
            conn.commit()

        # 3️⃣ Re‑fetch
        cur.execute("""
          SELECT id, text, completed, priority, assigned_to,
                 screenshot_url, user_email, created_at, updated_at
          FROM tasks WHERE id = %s
        """, (task_id,))
        row = cur.fetchone()
        cols = [c[0] for c in cur.description]
        cur.close()
        conn.close()

        result = dict(zip(cols, row))
        logger.debug("<<< UPDATED ROW: %r", result)
        return TaskOut(**result)

    






@app.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: int):
    conn = get_db_connection()
    cur  = conn.cursor()
    cur.execute("DELETE FROM tasks WHERE id = %s", (task_id,))
    conn.commit()
    cur.close()
    conn.close()
    return



# New model for just flipping completed on/off
class CompletedUpdate(BaseModel):
    completed: bool

# Dedicated endpoint to set completed
@app.put("/tasks/{task_id}/completed", response_model=TaskOut)
def set_task_completed(
    task_id: int,
    upd: CompletedUpdate = Body(...),
):
    now  = datetime.now(timezone.utc)
    conn = get_db_connection()
    cur  = conn.cursor()

    # Run an UPDATE that returns the new row in one go
    cur.execute(
        """
        UPDATE tasks
           SET completed  = %s,
               updated_at = %s
         WHERE id        = %s
       RETURNING id, text, completed, priority, assigned_to,
                 screenshot_url, user_email, created_at, updated_at
        """,
        (upd.completed, now, task_id),
    )
    row = cur.fetchone()
    conn.commit()

    if not row:
        cur.close()
        conn.close()
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    cols   = [c[0] for c in cur.description]
    result = dict(zip(cols, row))

    cur.close()
    conn.close()

    return TaskOut(**result)




