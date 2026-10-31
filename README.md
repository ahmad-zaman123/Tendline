# Deskhand

An AI customer support agent that answers from help docs, looks up orders through tool calls, and hands off to a person when it should not answer alone. Staff get a queue, the full transcript and a record of everything the agent did.

The demo company, Northwind Desks, and its data are made up.

## Safeguards

- **Customer scoping in code:** the customer comes from the signed-in session, never from model output, so a manipulated model can't read another customer's orders.
- **Real login:** scrypt-hashed passwords, signed session tokens, separate customer and staff roles, and a temporary lock after repeated failed sign-ins.
- **Automatic hand-off:** model errors, search failures, empty replies and runaway tool loops all go to the staff queue.
- **Audit trail:** every tool call and its result is stored for staff to review.
- **Rate limits:** sign-ins, demo creation and chat are limited per visitor, with a daily cap on model use, stored in Postgres so they hold across serverless instances.
- **Private demos:** one-click demo visitors get their own workspace, and every staff route checks it.
- **Sources:** answers show the help-doc passages the agent retrieved.
- **Model fallback:** if the main Gemini model is overloaded, rate-limited or retired, the request moves to a second model before handing off.
- **Hybrid search:** pgvector handles reworded questions, and Postgres full-text search makes exact codes like part numbers rank first.

## Stack

FastAPI, PostgreSQL with pgvector, Google Gemini for chat and embeddings, React with Vite.

## Run it locally

No Docker needed. `scripts/dev_db.py` starts a bundled Postgres with pgvector.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env                # fill in the values
.venv/bin/python scripts/dev_db.py  # put the printed URL in .env as DATABASE_URL
.venv/bin/python -m app.ingest      # creates tables and demo accounts, indexes the docs
.venv/bin/uvicorn asgi:app --port 8020

cd frontend
npm install
npm run dev                         # http://127.0.0.1:5173
```

Local accounts use `DEMO_PASSWORD`: `sara@example.com` and `omar@example.com` are customers, and `agent@example.com` is staff.

Set `DEMO_MODE=1` to turn on `/demo`, one-click demo sign-in and two public logins with the password `demo1234`: `demo@deskhand.app` (customer) and `support@deskhand.app` (support team). Each sign-in gets a private customer and support agent who can only see each other, deleted after a day.

Set `GOOGLE_CLIENT_ID` and `VITE_GOOGLE_CLIENT_ID` to turn on Google sign-in. It only ever creates customer accounts. Support team accounts sign in with a password.

## Hosted database

Create a Postgres database with pgvector (Neon works), set `DATABASE_URL` to its connection string, and run `python -m app.ingest`.

## Tests

```bash
.venv/bin/python -m pytest
```

The tests run against a real local Postgres with a scripted model and embeddings, so they need no API key.

## Status

Not deployed yet.
