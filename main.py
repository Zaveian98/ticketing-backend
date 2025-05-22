from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import bcrypt
from db import get_db_connection
from datetime import datetime, timezone
from typing import Optional, List
import logging
from fastapi import Form, File, UploadFile
from email_helper import send_email
from db import archive_ticket_in_db, get_user_email_for_ticket
from fastapi import BackgroundTasks
from email_helper import send_welcome_email
from jinja2 import Environment, FileSystemLoader
import os
import json



templates_dir = os.path.join(os.path.dirname(__file__), "templates")
jinja_env    = Environment(loader=FileSystemLoader(templates_dir))

# configure root logger at DEBUG (you can bump to INFO later)
logging.basicConfig(level=logging.DEBUG)
# grab Uvicorn’s “error” logger so messages show up in its console
logger = logging.getLogger("uvicorn.error")



from fastapi.staticfiles import StaticFiles      

BASE_DIR    = os.path.dirname(__file__)
STATIC_DIR  = os.path.join(BASE_DIR, "static")
UPLOADS_DIR = os.path.join(STATIC_DIR, "uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)

app = FastAPI(debug=True)                        

# … your CORS setup, routes, etc. …

# Serve ./static under /static
app.mount(
    "/static",
    StaticFiles(directory="static"),
    name="static"
)



origins = [
    "http://localhost:5173",                 # Vite dev
    "https://support.msistaff.com",           # Prod UI
    "https://ticketing-app-z0gp.onrender.com" # Render preview
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,    # ← use your list here, not ["*"]
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,   # only if you actually need cookies
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
    role: str = "User" 
    send_welcome_email: bool = False

class LoginRequest(BaseModel):
    email: str
    password: str

# Input model for creating a ticket
class TicketIn(BaseModel):
    title: str
    description: str
    submitted_by: str
    cc_email: Optional[str] = None 
    status: str = "Open"
    priority: str = "Medium"
    location: Optional[str] = None

# Output model for returning tickets
class TicketOut(BaseModel):
    id: int
    title: str
    description: str
    submitted_by: str
    submitted_by_name: str
    cc_email: Optional[str] = None
    status: str
    priority: str
    location: Optional[str]
    created_at: datetime
    updated_at: datetime
    archived: bool
    screenshots: List[str]  # ← list of URLs
    assigned_to: Optional[str] = None
    
    # ─── Define your update model *after* all the BaseModel outputs ───
class TicketUpdate(BaseModel):
    title:        Optional[str]   = None
    description:  Optional[str]   = None
    status:       Optional[str]   = None
    priority:     Optional[str]   = None
    updated_at:   Optional[datetime] = None
    assigned_to:  Optional[str]   = None
    archived:     Optional[bool]   = None

    
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
def register_user(user: RegisterRequest, background_tasks: BackgroundTasks):
    # 1️⃣ Insert the new user
    conn   = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT 1 FROM users WHERE email = %s", (user.email,))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="User already exists")

        hashed_pw = bcrypt.hashpw(user.password.encode(), bcrypt.gensalt()).decode()
        cursor.execute(
            """
            INSERT INTO users
              (first_name, last_name, email, company, password, role)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (user.first_name, user.last_name, user.email,
             user.company, hashed_pw, user.role),
        )
        conn.commit()

    finally:
        cursor.close()
        conn.close()

        # 2️⃣ Send styled “set password” email *only if* requested
    if user.send_welcome_email:
        logger.debug(
            "Register_user: scheduling welcome email to %s with role %s",
            user.email,
            user.role
        )
        background_tasks.add_task(
            send_welcome_email,
            user.email,
            user.first_name,
            user.password,
            f"https://support.msistaff.com/change-password?email={user.email}"
        )



    # 3️⃣ Return success message
    return {"message": "User registered successfully"}



# ✅ /login route
@app.post("/login")
def login_user(login: LoginRequest):
    conn = get_db_connection()
    cursor = conn.cursor()

    # pull password hash, role, and first_name
    cursor.execute(
        "SELECT password, role, first_name FROM users WHERE email = %s",
        (login.email,)
    )
    row = cursor.fetchone()
    if not row:
        cursor.close()
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")

    hashed_pw, role, first_name = row

    # verify password
    if not bcrypt.checkpw(login.password.encode("utf-8"), hashed_pw.encode("utf-8")):
        cursor.close()
        conn.close()
        raise HTTPException(status_code=401, detail="Incorrect password")

    cursor.close()
    conn.close()

    # return role + first_name
    return {
        "message": "Login successful",
        "role": role,
        "first_name": first_name
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

    # 1️⃣ Query tickets + user names
    sql = """
    SELECT
      t.id,
      t.title,
      t.description,
      t.submitted_by,
      u.first_name,
      u.last_name,
      t.status,
      t.priority,
      t.location,
      t.assigned_to,
      t.cc_email,
      t.created_at,
      t.updated_at,
      t.archived,
      t.screenshot
    FROM tickets AS t
    LEFT JOIN users AS u
      ON t.submitted_by = u.email
    WHERE t.archived = %s
    """
    params = [archived]

    if user_email:
        sql += " AND t.submitted_by = %s"
        params.append(user_email)

    sql += " ORDER BY t.created_at DESC"

    cur.execute(sql, params)
    cols = [c[0] for c in cur.description]
    rows = cur.fetchall()
    cur.close()
    conn.close()

    tickets: list[TicketOut] = []
    for row in rows:
        data = dict(zip(cols, row))

        # 2️⃣ Build the `submitted_by_name` field
        first = data.pop("first_name") or ""
        last  = data.pop("last_name") or ""
        data["submitted_by_name"] = (first + " " + last).strip()

        # 3️⃣ Build the `screenshots` list safely
        raw = data.pop("screenshot")
        if not raw:
            screenshots = []
        else:
            try:
                parsed = json.loads(raw)
                screenshots = parsed if isinstance(parsed, list) else [parsed]
            except (ValueError, json.JSONDecodeError):
                screenshots = [raw]
        data["screenshots"] = screenshots

        tickets.append(TicketOut(**data))

    return tickets




from fastapi import BackgroundTasks, Form, File, UploadFile
from typing import List, Optional

@app.patch("/tickets/{ticket_id}", response_model=TicketOut)
def patch_ticket(ticket_id: int, changes: TicketUpdate, background_tasks: BackgroundTasks,):
    conn = get_db_connection()
    cur  = conn.cursor()

    cur.execute("SELECT * FROM tickets WHERE id = %s", (ticket_id,))
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        raise HTTPException(status_code=404, detail="Ticket not found")

    update_data = {k: v for k, v in changes.dict().items() if v is not None}
    if update_data:
        set_clause = ", ".join(f"{k} = %s" for k in update_data.keys())
        now        = datetime.now(timezone.utc)
        params     = list(update_data.values()) + [now, ticket_id]
        cur.execute(
            f"UPDATE tickets SET {set_clause}, updated_at = %s WHERE id = %s",
            params
        )
        conn.commit()

    cur.execute("SELECT * FROM tickets WHERE id = %s", (ticket_id,))
    updated_row = cur.fetchone()
    cols        = [c[0] for c in cur.description]

    cur.close()
    conn.close()

    # Build a dict of the updated row
    result = dict(zip(cols, updated_row))
    result["submitted_by_name"] = result["submitted_by"]

    # ── Send notification if status changed to Resolved or Closed ──
    new_status = result.get("status")
    if new_status in ("Resolved", "Closed"):
        # ← only this block changed:
        html = f"""\
<!DOCTYPE html>
<html>
  <head>
    <meta charset="UTF-8" />
    <title>Your Ticket #{ticket_id} {new_status}</title>
  </head>
  <body style="margin:0;padding:0;background:#f2f2f2;font-family:Arial,sans-serif;">
    <table width="100%" cellpadding="0" cellspacing="0">
      <tr>
        <td align="center">
          <table width="600" cellpadding="0" cellspacing="0"
                 style="background:#ffffff;border-radius:8px;overflow:hidden;margin:20px auto;">
            <tr style="background:#0052cc;">
              <td align="center" style="padding:20px;">
                <h1 style="color:#ffffff;font-size:24px;margin:0;">
                  Your Ticket #{ticket_id} Is {new_status}! 🎉
                </h1>
              </td>
            </tr>
            <tr>
              <td style="padding:20px;color:#333333;line-height:1.6;">
                <p>Hi there,</p>
                <p>Your ticket titled “<strong>{result["title"]}</strong>” has been {new_status.lower()}.</p>
                <p>You can view the details or open a follow-up here:</p>
                <p style="text-align:center;margin:30px 0;">
                  <a
                    href="https://support.msistaff.com/ticketboard?user_email={result["submitted_by"]}&ticket_id={ticket_id}"
                    style="
                      background:#0052cc;
                      color:#ffffff;
                      text-decoration:none;
                      padding:12px 24px;
                      border-radius:4px;
                      display:inline-block;
                    "
                  >
                    View Your Ticket
                  </a>
                </p>
                <p>Thanks,<br/>The MSI Support Team</p>
              </td>
            </tr>
            <tr>
              <td style="background:#f2f2f2;color:#999999;font-size:12px;text-align:center;padding:10px;">
                &copy; {datetime.now(timezone.utc).year} MSI Staff Inc. — 
                <a href="https://support.msistaff.com" style="color:#0052cc;text-decoration:none;">
                  Support Portal
                </a>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""
        subject = f"Your Ticket #{ticket_id} {new_status}"
        background_tasks.add_task(
            send_email,
            result["submitted_by"],
            subject,
            html
        )

    print("Updated ticket:", result)
    return TicketOut(**result)





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


class UserOut(BaseModel):
    first_name: str
    last_name:  str
    email:      str
    role:       str
    company:    str



@app.patch("/tickets/{ticket_id}", response_model=TicketOut)
def patch_ticket(ticket_id: int, changes: TicketUpdate, background_tasks: BackgroundTasks):
    conn = get_db_connection()
    cur  = conn.cursor()

    cur.execute("SELECT * FROM tickets WHERE id = %s", (ticket_id,))
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        raise HTTPException(status_code=404, detail="Ticket not found")

    update_data = {k: v for k, v in changes.dict().items() if v is not None}
    if update_data:
        set_clause = ", ".join(f"{k} = %s" for k in update_data.keys())
        now        = datetime.now(timezone.utc)
        params     = list(update_data.values()) + [now, ticket_id]
        cur.execute(
            f"UPDATE tickets SET {set_clause}, updated_at = %s WHERE id = %s",
            params
        )
        conn.commit()

    # ── Re-fetch the updated row ──
    cur.execute("SELECT * FROM tickets WHERE id = %s", (ticket_id,))
    updated_row = cur.fetchone()
    cols        = [c[0] for c in cur.description]

    cur.close()
    conn.close()

    # ── Build a dict of the updated row ──
    result = dict(zip(cols, updated_row))

    # ── Build the `submitted_by_name` field ──
    result["submitted_by_name"] = result["submitted_by"]

    # ── **NEW**: parse the raw JSON screenshot column into your screenshots list ──
    raw = result.pop("screenshot", None) or "[]"
    try:
        parsed = json.loads(raw)
        # if it's already a list, use it; otherwise wrap single URL in a list
        result["screenshots"] = parsed if isinstance(parsed, list) else [parsed]
    except (ValueError, TypeError):
        # if JSON is invalid for some reason, just give an empty list
        result["screenshots"] = []

    # ── Send notification if status changed to Resolved or Closed ──
    new_status = result.get("status")
    if new_status in ("Resolved", "Closed"):
        html = jinja_env.get_template("status_notification.html").render(
            ticket_id    = ticket_id,
            title        = result["title"],
            status       = new_status,
            status_lower = new_status.lower(),
            submitted_by = result["submitted_by"],
            year         = datetime.now(timezone.utc).year,
        )
        subject = f"Your Ticket #{ticket_id} {new_status}"
        background_tasks.add_task(
            send_email,
            result["submitted_by"],
            subject,
            html
        )

    print("Updated ticket:", result)

    # ── Finally, return the updated ticket ──
    return TicketOut(**result)


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

from fastapi import HTTPException
import json

@app.get("/tickets/{ticket_id}", response_model=TicketOut)
def get_ticket(ticket_id: int):
    conn = get_db_connection()
    cur  = conn.cursor()
    cur.execute(
        """
        SELECT
          id,
          title,
          description,
          submitted_by,
          cc_email,
          status,
          priority,
          location,
          assigned_to,
          created_at,
          updated_at,
          archived,
          screenshot
        FROM tickets
        WHERE id = %s
        """,
        (ticket_id,)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Ticket not found")

    # 1) Zip columns + row into a dict
    cols   = [
      "id","title","description","submitted_by","cc_email","status","priority",
      "location","assigned_to","created_at","updated_at","archived","screenshot"
    ]
    result = dict(zip(cols, row))

    # 2) Build submitted_by_name
    result["submitted_by_name"] = result["submitted_by"]

    # 3) Parse the JSON‐encoded screenshot field into your list
    raw = result.pop("screenshot") or "[]"
    result["screenshots"] = json.loads(raw)

    # 4) Return
    return TicketOut(**result)


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

from pydantic import EmailStr

class PasswordChange(BaseModel):
    email:     EmailStr
    new_password: str

@app.post("/change-password")
def change_password(data: PasswordChange):
    # 1. Hash the new password
    hashed = bcrypt.hashpw(data.new_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    # 2. Update the user’s password in the DB
    conn   = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET password = %s WHERE email = %s", (hashed, data.email))
    if cursor.rowcount == 0:
        cursor.close()
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")
    conn.commit()
    cursor.close()
    conn.close()

    return {"message": "Password updated successfully"}




# ─── Health check ────────────────────────────────────────────────
@app.get("/", include_in_schema=False)
def root():
    return {"status": "ok"}



@app.post("/tickets/{ticket_id}/cancel")
async def cancel_ticket(ticket_id: int):
    # 1) Mark the ticket as archived in the database
    success = archive_ticket_in_db(ticket_id)
    if not success:
        raise HTTPException(status_code=404, detail="Ticket not found")

    user_email = get_user_email_for_ticket(ticket_id)
    print(f"[CancelRoute] user_email = {user_email}")   # log the address

    if user_email:
        try:
            print(f"[CancelRoute] Attempting to send email to {user_email}")
            send_email(
                to=user_email,
                subject="Your ticket has been canceled",
                text=f"Hello,\n\nYour ticket #{ticket_id} has been canceled.\n\n—The MSI Support Team",
                html=f"<p>Hello,</p><p>Your ticket #{ticket_id} has been <strong>canceled</strong>.</p><p>—The MSI Support Team</p>"
            )
            print("[CancelRoute] Email send succeeded")
        except Exception as e:
            print("[CancelRoute] Email send ERROR:", repr(e))

    return {"status": "canceled"}



