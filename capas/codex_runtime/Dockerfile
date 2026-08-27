FROM oc8-agent-runtime-base:latest
RUN npm install -g @openai/codex
# LAST, after the global install -- see claude_code_runtime/Dockerfile.base
# for why this cannot live in the shared base. `node` is the image's own
# uid-1000 user.
USER node
