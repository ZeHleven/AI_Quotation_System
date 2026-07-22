## Summary

- 

## Test Plan

- [ ] Backend: `cd AI_Middle_Office && python -m compileall app tests`
- [ ] Backend: `cd AI_Middle_Office && python -m pytest -q`
- [ ] Frontend: `cd ai-web && npm ci && npm run build`
- [ ] Manual smoke: login, budget project list, import/mapping page, pricing draft page

## Risk Checklist

- [ ] No secrets, tokens, private keys, production exports, or `.env` files included
- [ ] Database changes include an Alembic migration
- [ ] Long-running quote or import flows have a cancel/retry/recovery path
- [ ] User-facing Chinese copy has been checked in the actual UI
