// Discord shortcodes the bot writes in its own text (people's typed emoji
// already arrive as Unicode). Unknown names are left literal.
const TABLE = {
  x: "❌", white_check_mark: "✅", warning: "⚠️", exclamation: "❗", information_source: "ℹ️",
  partying_face: "🥳", hourglass: "⌛", wastebasket: "🗑️", incoming_envelope: "📨", grey_question: "❔",
  wave: "👋", pencil: "📝", no_entry_sign: "🚫", mag_right: "🔎", free: "🆓", door: "🚪",
  biohazard: "☣️", sparkles: "✨", key: "🔑", jigsaw: "🧩", hammer_and_wrench: "🛠️",
  frame_photo: "🖼️", file_folder: "📁", dog: "🐶", broom: "🧹", tada: "🎉", thumbsup: "👍",
  heart: "❤️", eyes: "👀",
};

export const replaceShortcodes = (text) =>
  String(text ?? "").replace(/:([a-z0-9_]+):/g, (all, name) => (Object.hasOwn(TABLE, name) ? TABLE[name] : all));
