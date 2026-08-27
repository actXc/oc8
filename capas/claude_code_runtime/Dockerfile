FROM oc8-agent-runtime-base:latest
RUN npm install -g @anthropic-ai/claude-code
# LAST, after the global install -- see Dockerfile.base for why this cannot
# live in the shared base. `node` is the image's own uid-1000 user.
USER node
