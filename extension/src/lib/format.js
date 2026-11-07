// Map the server's message JSON (bot/web/data.py get_channel_messages) to a view.
export function messageView(msg) {
  return {
    author: msg.author_name,
    avatar: msg.author_avatar ?? null,
    text: msg.content ?? "",
    time: msg.created_at,
    hasEmbeds: Array.isArray(msg.embeds) && msg.embeds.length > 0,
  };
}

export function isBangCommand(text) {
  return text.trimStart().startsWith("!");
}

const ERROR_TEXT = {
  empty: "Type a message first.",
  too_long: "Message is too long (2000 characters max).",
  command: "Run commands in Discord.",
  archived: "This hunt is archived; replies are closed.",
  forbidden: "You don't have permission to post here.",
  not_found_or_forbidden: "You don't have access to this channel.",
  send_failed: "Discord didn't accept the message — try again or post in Discord.",
  csrf: "Session expired — reload the dashboard and try again.",
};

// Short inline message for a server error code. Unknown codes get a generic
// fallback that never echoes server text.
export function errorText(code) {
  return Object.hasOwn(ERROR_TEXT, code) ? ERROR_TEXT[code] : "Could not send the message.";
}
