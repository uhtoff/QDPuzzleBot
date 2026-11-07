// Map the server's message JSON (bot/web/data.py get_channel_messages) to a view.
export function messageView(msg) {
  return {
    author: msg.author_name,
    avatar: msg.author_avatar ?? null,
    text: msg.content ?? "",
    time: msg.created_at,
  };
}

export function isBangCommand(text) {
  return text.trimStart().startsWith("!");
}
