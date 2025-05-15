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
  allow_credentials=True,
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

    # 2️⃣ Send the “set password” email
    try:
        html = f"""
  <h1>Welcome to MSI Ticketing</h1>
  <p>Hi {user.first_name},</p>
  <p>Your account has been created with a temporary password:</p>
  <p><strong>{user.password}</strong></p>
  <p>
    Please
    <a href="https://support.msistaff.com/change-password?email={user.email}">
      click here
    </a>
    to set your permanent password.
  </p>
"""

        send_email(
            to=user.email,
            subject="Your MSI Ticketing Account — Set Your Password",
            html=html
        )
    except Exception as e:
        logger.error("Failed to send temp password email to %s: %s", user.email, e, exc_info=True)

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

from datetime import datetime, timezone

@app.post("/tickets", response_model=TicketOut)
def create_ticket(ticket: TicketIn):
    """Create a new ticket and email support, the submitter, and optional CC."""
    now = datetime.now(timezone.utc)

    # 1️⃣ Insert into DB
    conn = get_db_connection()
    cur  = conn.cursor()
    cur.execute(
        """
        INSERT INTO tickets
          (title, description, submitted_by, status, priority,
           created_at, updated_at, screenshot, cc_email)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            ticket.title,
            ticket.description,
            ticket.submitted_by,
            ticket.status,
            ticket.priority,
            now,
            now,
            ticket.screenshot,
            ticket.cc_email,
        ),
    )
    ticket_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()

    # 2️⃣ Notify support with styled HTML
    support_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>New Ticket #{ticket_id}</title>
  <style>
    body {{ margin:0; padding:0; background:linear-gradient(135deg,#e0eafc,#cfdef3); font-family:Arial,sans-serif; }}
    .card {{ max-width:500px; margin:40px auto; background:#fff; border-radius:8px; box-shadow:0 4px 12px rgba(0,0,0,0.15); overflow:hidden; }}
    .header {{ background:#0052cc; color:#fff; padding:16px; text-align:center; }}
    .header h1 {{ margin:0; font-size:1.4rem; }}
    .content {{ padding:24px; color:#333; line-height:1.6; }}
    .content ul {{ padding-left:20px; }}
    .content a.button {{ display:inline-block; margin-top:16px; padding:10px 18px; background:#0052cc; color:#fff; text-decoration:none; border-radius:4px; font-weight:bold; }}
    .footer {{ text-align:center; padding:12px; font-size:12px; color:#777; background:#f4f4f4; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="header">
      <h1>New Ticket Submitted</h1>
    </div>
    <div class="content">
      <p>Hello Support Team,</p>
      <p>A new ticket has been created:</p>
      <ul>
        <li><strong>Ticket #:</strong> {ticket_id}</li>
        <li><strong>Title:</strong> {ticket.title}</li>
        <li><strong>Description:</strong> {ticket.description}</li>
        <li><strong>Submitted by:</strong> {ticket.submitted_by}</li>
      </ul>
      <a href="https://ticketing-app-z0gp.onrender.com/tickets/{ticket_id}" class="button">
        View Ticket
      </a>
    </div>
    <div class="footer">
      &copy; {now.year} MSI Staff Inc. — <a href="https://support.msistaff.com">Support Portal</a>
    </div>
  </div>
</body>
</html>
"""
    try:
        send_email(
            to="support@msistaff.com",
            subject=f"[MSI] New Ticket #{ticket_id} Submitted",
            html=support_html
        )
    except Exception as e:
        logger.error("Failed to notify support: %s", e, exc_info=True)

    # 3️⃣ Notify the submitter
    user_html = f"""
      <h1>Your ticket #{ticket_id} has been received</h1>
      <p>We’ve received your ticket &quot;{ticket.title}&quot; and will notify you when it’s resolved or closed.</p>
    """
    try:
        send_email(
            to=ticket.submitted_by,
            subject=f"Your Ticket #{ticket_id} Received",
            html=user_html
        )
    except Exception as e:
        logger.error("Failed to send confirmation to user %s: %s",
                     ticket.submitted_by, e, exc_info=True)

    # 4️⃣ CC notification (if provided)
    if ticket.cc_email:
        cc_html = f"""
          <h1>Ticket #{ticket_id} Submitted (CC)</h1>
          <p>You were CC’d on ticket &quot;<strong>{ticket.title}</strong>&quot; submitted by {ticket.submitted_by}.</p>
          <p><strong>Description:</strong> {ticket.description}</p>
        """
        try:
            send_email(
                to=ticket.cc_email,
                subject=f"You were CC’d on Ticket #{ticket_id}",
                html=cc_html
            )
        except Exception as e:
            logger.error("Failed to send CC to %s: %s",
                         ticket.cc_email, e, exc_info=True)

    # 5️⃣ Return the new ticket record
    return TicketOut(
        id=ticket_id,
        title=ticket.title,
        description=ticket.description,
        submitted_by=ticket.submitted_by,
        cc_email=ticket.cc_email,
        status=ticket.status,
        priority=ticket.priority,
        assigned_to=None,
        created_at=now,
        updated_at=now,
        archived=False,
        screenshot=ticket.screenshot,
    )




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

    # ── Send notification if status changed to Resolved or Closed ──
    new_status = result.get("status")
    if new_status in ("Resolved", "Closed"):
        html = f"""
            <h1>Ticket #{ticket_id} {new_status}</h1>
            <p>Your ticket "<strong>{result['title']}</strong>" has been <strong>{new_status.lower()}</strong>.</p>
        """
        send_email(
            to=result["submitted_by"],
            subject=f"Your Ticket #{ticket_id} {new_status}",
            html=html
        )

    # Finally, return the updated ticket
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

@app.get("/tickets/{ticket_id}", response_model=TicketOut)
def get_ticket(ticket_id: int):
    """
    Retrieve a single ticket by ID.
    """
    conn = get_db_connection()
    cur  = conn.cursor()
    cur.execute(
        """
        SELECT id, title, description, submitted_by, cc_email, status, priority,
               assigned_to, created_at, updated_at, archived, screenshot
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

    cols = [
        "id","title","description","submitted_by","cc_email","status","priority",
        "assigned_to","created_at","updated_at","archived","screenshot"
    ]
    return TicketOut(**dict(zip(cols, row)))

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



