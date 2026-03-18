// scripts/discord.mjs
// Sends a message to a Discord channel via webhook.
// Set DISCORD_WEBHOOK_URL in your .env file.
// Automatically chunks messages over Discord's 2000-char limit.

const MAX_LEN = 2000;

async function postChunk(url, content) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });

  if (!res.ok) {
    const txt = await res.text().catch(() => "");
    console.warn(`  [discord] Webhook failed: ${res.status} ${txt.slice(0, 100)}`);
  }
  return res.ok;
}

export async function sendDiscord(content) {
  const url = process.env.DISCORD_WEBHOOK_URL;
  if (!url) {
    console.warn("  [discord] No DISCORD_WEBHOOK_URL set — skipping");
    return;
  }

  if (!content || !content.trim()) {
    console.warn("  [discord] Empty message — skipping");
    return;
  }

  try {
    // If it fits, send it directly
    if (content.length <= MAX_LEN) {
      const ok = await postChunk(url, content);
      if (ok) console.log("  [discord] Message sent");
      return;
    }

    // Otherwise split on newlines, respecting the limit
    const lines = content.split("\n");
    let chunk = "";
    let sent = 0;

    for (const line of lines) {
      if (chunk.length + line.length + 1 > MAX_LEN) {
        if (chunk) {
          await postChunk(url, chunk);
          sent++;
        }
        chunk = line;
      } else {
        chunk += (chunk ? "\n" : "") + line;
      }
    }

    if (chunk) {
      await postChunk(url, chunk);
      sent++;
    }

    console.log(`  [discord] Message sent (${sent} chunk${sent > 1 ? "s" : ""})`);
  } catch (e) {
    console.warn("  [discord] Error:", e.message);
  }
}
