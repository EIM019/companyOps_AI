import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  CalendarPlus,
  Check,
  Database,
  FileUp,
  LayoutDashboard,
  Link,
  LogOut,
  Plus,
  Send,
  ShieldCheck,
  UserPlus,
  Users,
  X,
} from "lucide-react";
import { api, clearToken, getToken, setToken, streamChat } from "./lib/api";
import "./styles.css";

function ActionCard({ action, onDecision }) {
  return (
    <div className="action-card">
      <div>
        <span className="eyebrow">Pending action</span>
        <h3>{action.type.replaceAll("_", " ")}</h3>
      </div>
      <pre>{JSON.stringify(action.payload, null, 2)}</pre>
      <div className="button-row">
        <button className="icon-button approve" title="Confirm action" onClick={() => onDecision(action.id, "confirm")}>
          <Check size={18} />
        </button>
        <button className="icon-button reject" title="Reject action" onClick={() => onDecision(action.id, "reject")}>
          <X size={18} />
        </button>
      </div>
    </div>
  );
}

function Message({ message }) {
  return (
    <div className={`message ${message.role}`}>
      <span>{message.role === "user" ? "You" : "Assistant"}</span>
      <p>{message.content}</p>
    </div>
  );
}

function LoginScreen({ onLogin }) {
  const [mode, setMode] = useState("login");
  const [form, setForm] = useState({
    email: "owner@acme.test",
    password: "demo12345",
    token: "",
    name: "",
  });
  const [error, setError] = useState("");

  async function submit(event) {
    event.preventDefault();
    setError("");
    try {
      const path = mode === "login" ? "/auth/login" : "/auth/accept-invite";
      const payload =
        mode === "login"
          ? { email: form.email, password: form.password }
          : { token: form.token, name: form.name, password: form.password };
      const data = await api(path, { method: "POST", body: JSON.stringify(payload) });
      setToken(data.auth_token);
      onLogin();
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <main className="auth-shell">
      <section className="auth-panel">
        <div className="brand large">
          <ShieldCheck size={28} />
          <div>
            <strong>CompanyOps AI</strong>
            <span>Internal assistant platform</span>
          </div>
        </div>
        <div className="segmented">
          <button className={mode === "login" ? "selected" : ""} onClick={() => setMode("login")}>Login</button>
          <button className={mode === "invite" ? "selected" : ""} onClick={() => setMode("invite")}>Accept invite</button>
        </div>
        <form className="auth-form" onSubmit={submit}>
          {mode === "login" ? (
            <input value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} placeholder="Email" />
          ) : (
            <>
              <input value={form.token} onChange={(e) => setForm({ ...form, token: e.target.value })} placeholder="Invite token" />
              <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Full name" />
            </>
          )}
          <input
            type="password"
            value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })}
            placeholder="Password"
          />
          {error ? <div className="notice danger">{error}</div> : null}
          <button className="primary-button">Continue</button>
        </form>
        <p className="muted">Demo owner: owner@acme.test / demo12345</p>
      </section>
    </main>
  );
}

function AdminDashboard({ me, setNotice }) {
  const [summary, setSummary] = useState(null);
  const [users, setUsers] = useState([]);
  const [invites, setInvites] = useState([]);
  const [integrations, setIntegrations] = useState([]);
  const [invite, setInvite] = useState({ email: "", role: "member" });

  async function loadAdmin() {
    const [summaryData, userData, inviteData, integrationData] = await Promise.all([
      api("/admin/summary"),
      api("/admin/users"),
      api("/admin/invites"),
      api("/admin/integrations"),
    ]);
    setSummary(summaryData);
    setUsers(userData.users);
    setInvites(inviteData.invites);
    setIntegrations(integrationData.integrations);
  }

  useEffect(() => {
    if (me?.user?.role !== "member") loadAdmin().catch((err) => setNotice(err.message));
  }, [me]);

  async function sendInvite(event) {
    event.preventDefault();
    const data = await api("/auth/invites", { method: "POST", body: JSON.stringify(invite) });
    setInvite({ email: "", role: "member" });
    setNotice(`Invite created for ${data.email}. Token: ${data.token}`);
    await loadAdmin();
  }

  async function updateRole(userId, role) {
    await api(`/admin/users/${userId}`, { method: "PATCH", body: JSON.stringify({ role }) });
    await loadAdmin();
  }

  async function reindex() {
    const data = await api("/admin/reindex", { method: "POST" });
    setNotice(`Knowledge index refreshed: ${data.chunks} chunks.`);
  }

  if (me?.user?.role === "member") {
    return <div className="empty-state">Admin access is available to owners and admins.</div>;
  }

  return (
    <section className="admin-view">
      <header>
        <div>
          <span className="eyebrow">Admin dashboard</span>
          <h1>{summary?.company?.name || "Company"}</h1>
        </div>
        <button className="secondary-button" onClick={reindex}>
          <Database size={16} /> Reindex knowledge
        </button>
      </header>

      <div className="metric-grid">
        {summary
          ? Object.entries(summary.counts).map(([label, value]) => (
              <div className="metric" key={label}>
                <strong>{value}</strong>
                <span>{label.replaceAll("_", " ")}</span>
              </div>
            ))
          : null}
      </div>

      <div className="admin-grid">
        <section>
          <div className="panel-header">
            <h2><Users size={16} /> Users</h2>
          </div>
          <div className="table-list">
            {users.map((user) => (
              <div key={user.id}>
                <span>{user.name}<small>{user.email}</small></span>
                <select value={user.role} disabled={user.id === me.user.id} onChange={(e) => updateRole(user.id, e.target.value)}>
                  <option value="owner">owner</option>
                  <option value="admin">admin</option>
                  <option value="member">member</option>
                </select>
              </div>
            ))}
          </div>
        </section>

        <section>
          <div className="panel-header">
            <h2><UserPlus size={16} /> Invites</h2>
          </div>
          <form className="inline-form" onSubmit={sendInvite}>
            <input value={invite.email} onChange={(e) => setInvite({ ...invite, email: e.target.value })} placeholder="teammate@company.com" />
            <select value={invite.role} onChange={(e) => setInvite({ ...invite, role: e.target.value })}>
              <option value="member">member</option>
              <option value="admin">admin</option>
            </select>
            <button className="icon-button send" title="Create invite"><Plus size={16} /></button>
          </form>
          <div className="table-list">
            {invites.map((item) => (
              <div key={item.id}>
                <span>{item.email}<small>{item.accepted_at ? "accepted" : `token: ${item.token}`}</small></span>
                <strong>{item.role}</strong>
              </div>
            ))}
          </div>
        </section>

        <section>
          <div className="panel-header">
            <h2><Link size={16} /> Integrations</h2>
          </div>
          <div className="table-list">
            {integrations.length ? integrations.map((item) => (
              <div key={item.id}>
                <span>{item.provider}<small>{item.updated_at}</small></span>
                <strong>{item.status}</strong>
              </div>
            )) : <p className="muted">No integrations connected yet.</p>}
          </div>
        </section>
      </div>
    </section>
  );
}

function WorkspaceApp({ onLogout }) {
  const [view, setView] = useState("chat");
  const [me, setMe] = useState(null);
  const [threads, setThreads] = useState([]);
  const [activeThread, setActiveThread] = useState(null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [pendingActions, setPendingActions] = useState([]);
  const [projects, setProjects] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  const activeTitle = useMemo(
    () => threads.find((thread) => thread.id === activeThread)?.title || "Operations assistant",
    [threads, activeThread]
  );

  async function refreshWorkspace() {
    const [projectData, taskData] = await Promise.all([api("/projects"), api("/tasks")]);
    setProjects(projectData.projects);
    setTasks(taskData.tasks);
  }

  async function loadThreadMessages(threadId) {
    const data = await api(`/chat/threads/${threadId}/messages`);
    setMessages(data.messages);
  }

  async function bootstrap() {
    const [profile, threadData] = await Promise.all([api("/me"), api("/chat/threads")]);
    setMe(profile);
    let nextThreads = threadData.threads;
    if (nextThreads.length === 0) {
      const created = await api("/chat/threads", { method: "POST", body: JSON.stringify({ title: "Operations assistant" }) });
      nextThreads = [created.thread];
    }
    setThreads(nextThreads);
    setActiveThread(nextThreads[0].id);
    await loadThreadMessages(nextThreads[0].id);
    await refreshWorkspace();
  }

  useEffect(() => {
    bootstrap().catch((error) => setNotice(error.message));
  }, []);

  async function createThread() {
    const created = await api("/chat/threads", { method: "POST", body: JSON.stringify({ title: "New chat" }) });
    setThreads((current) => [created.thread, ...current]);
    setActiveThread(created.thread.id);
    setMessages([]);
  }

  async function sendMessage(event) {
    event.preventDefault();
    const content = input.trim();
    if (!content || !activeThread || busy) return;
    setBusy(true);
    setNotice("");
    setInput("");
    setMessages((current) => [...current, { role: "user", content }, { role: "assistant", content: "" }]);
    try {
      const final = await streamChat(activeThread, content, (token) => {
        setMessages((current) => {
          const next = [...current];
          next[next.length - 1] = { role: "assistant", content: token };
          return next;
        });
      });
      setPendingActions((current) => [...final.pending_actions, ...current]);
    } catch (error) {
      setNotice(error.message);
      setMessages((current) => current.slice(0, -1));
    } finally {
      setBusy(false);
    }
  }

  async function decideAction(actionId, decision) {
    const result = await api(`/actions/${actionId}/confirm`, { method: "POST", body: JSON.stringify({ decision }) });
    setPendingActions((current) => current.filter((action) => action.id !== actionId));
    setNotice(`Action ${result.action.status}.`);
    await refreshWorkspace();
  }

  async function uploadDocument(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    const data = await api("/documents/upload", { method: "POST", body: form });
    setNotice(`Uploaded and indexed ${data.document.filename}.`);
    event.target.value = "";
  }

  async function connect(provider) {
    const endpoint = provider === "google" ? "/integrations/google-calendar/connect" : "/integrations/trello/connect";
    const data = await api(endpoint, { method: "POST", body: JSON.stringify({ config: { mode: "demo" } }) });
    if (data.auth_url) {
      window.location.href = data.auth_url;
      return;
    }
    setNotice(`${data.integration.provider} connected.`);
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <ShieldCheck size={24} />
          <div>
            <strong>CompanyOps AI</strong>
            <span>{me?.company?.name || "Loading workspace"}</span>
          </div>
        </div>
        <button className={`nav-button ${view === "chat" ? "selected" : ""}`} onClick={() => setView("chat")}>
          <Send size={16} /> Assistant
        </button>
        <button className={`nav-button ${view === "admin" ? "selected" : ""}`} onClick={() => setView("admin")}>
          <LayoutDashboard size={16} /> Admin
        </button>
        {view === "chat" ? <button className="primary-button" onClick={createThread}><Plus size={17} /> New chat</button> : null}
        {view === "chat" ? (
          <nav className="thread-list">
            {threads.map((thread) => (
              <button key={thread.id} className={thread.id === activeThread ? "selected" : ""} onClick={() => { setActiveThread(thread.id); loadThreadMessages(thread.id); }}>
                {thread.title}
              </button>
            ))}
          </nav>
        ) : null}
        <button className="nav-button logout" onClick={onLogout}><LogOut size={16} /> Sign out</button>
      </aside>

      {view === "admin" ? (
        <AdminDashboard me={me} setNotice={setNotice} />
      ) : (
        <section className="chat-panel">
          <header>
            <div>
              <span className="eyebrow">Internal assistant</span>
              <h1>{activeTitle}</h1>
            </div>
            <div className="identity">{me?.user?.email}</div>
          </header>
          <div className="messages">
            {messages.length === 0 ? <div className="empty-state">Ask about company documents, projects, task creation, or calendar booking.</div> : messages.map((message, index) => <Message key={`${message.role}-${index}`} message={message} />)}
          </div>
          {notice ? <div className="notice">{notice}</div> : null}
          <form className="composer" onSubmit={sendMessage}>
            <input value={input} onChange={(event) => setInput(event.target.value)} placeholder="Ask CompanyOps AI..." />
            <button className="icon-button send" title="Send message" disabled={busy}><Send size={18} /></button>
          </form>
        </section>
      )}

      <aside className="workspace-panel">
        <section>
          <div className="panel-header"><h2>Action Queue</h2><span>{pendingActions.length}</span></div>
          {pendingActions.length ? pendingActions.map((action) => <ActionCard key={action.id} action={action} onDecision={decideAction} />) : <p className="muted">Write actions wait here for approval before changing company data.</p>}
        </section>
        <section>
          <div className="panel-header">
            <h2>Knowledge</h2>
            <label className="icon-button" title="Upload PDF, DOCX, or CSV"><FileUp size={18} /><input type="file" accept=".pdf,.docx,.csv" onChange={uploadDocument} /></label>
          </div>
          <p className="muted">Uploads are chunked and indexed for retrieval. OpenAI embeddings run when an API key is configured.</p>
        </section>
        <section>
          <div className="panel-header"><h2>Integrations</h2></div>
          <div className="button-row">
            <button className="secondary-button" onClick={() => connect("google")}><CalendarPlus size={16} /> Google</button>
            <button className="secondary-button" onClick={() => connect("trello")}><Link size={16} /> Trello</button>
          </div>
        </section>
        <section>
          <div className="panel-header"><h2>Projects</h2><span>{projects.length}</span></div>
          <div className="compact-list">{projects.map((project) => <div key={project.id}><strong>{project.name}</strong><span>{project.source}</span></div>)}</div>
        </section>
        <section>
          <div className="panel-header"><h2>Tasks</h2><span>{tasks.length}</span></div>
          <div className="compact-list">{tasks.slice(0, 6).map((task) => <div key={task.id}><strong>{task.title}</strong><span>{task.status}</span></div>)}</div>
        </section>
      </aside>
    </main>
  );
}

function App() {
  const [sessionVersion, setSessionVersion] = useState(0);
  if (!getToken()) return <LoginScreen onLogin={() => setSessionVersion((value) => value + 1)} />;
  return <WorkspaceApp key={sessionVersion} onLogout={() => { clearToken(); setSessionVersion((value) => value + 1); }} />;
}

createRoot(document.getElementById("root")).render(<App />);
