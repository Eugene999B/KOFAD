/** KOFAD inbound email gateway. Does NOT send external mail.
 * Configure KOFAD_INGEST_SECRET on BOTH this Worker and the Railway web service.
 * FALLBACK_EMAIL must be a verified Cloudflare Email Routing destination.
 * Leave routing rules targeting the current Gmail destination until tested.
 */
const limit = 1024 * 1024;
const hex = (bytes) => Array.from(new Uint8Array(bytes)).map(b => b.toString(16).padStart(2, "0")).join("");

export default {
  async email(message, env) {
    const fallback = env.FALLBACK_EMAIL || "kofadimpexenterprise@gmail.com";
    if (!env.KOFAD_INGEST_SECRET || env.KOFAD_INGEST_SECRET.length < 32) {
      await message.forward(fallback);
      return;
    }
    if (message.rawSize > limit) {
      await message.forward(fallback);
      return;
    }
    try {
      const recipient = String(message.to).trim().toLowerCase();
      if (!recipient.endsWith("@kofadimpex.com")) {
        await message.forward(fallback);
        return;
      }
      const raw = await new Response(message.raw).arrayBuffer();
      if (raw.byteLength > limit) {
        await message.forward(fallback);
        return;
      }
      const encoder = new TextEncoder();
      const stamp = String(Math.floor(Date.now() / 1000));
      const hash = hex(await crypto.subtle.digest("SHA-256", raw));
      const content = stamp + "\n" + recipient + "\n" + hash;
      const key = await crypto.subtle.importKey(
        "raw", encoder.encode(env.KOFAD_INGEST_SECRET),
        { name: "HMAC", hash: "SHA-256" }, false, ["sign"]
      );
      const signature = hex(await crypto.subtle.sign("HMAC", key, encoder.encode(content)));
      const result = await fetch(
        "https://staff.kofadimpex.com/email/ingest/",
        {
          method: "POST",
          headers: {
            "Content-Type": "message/rfc822",
            "X-Kofad-Recipient": recipient,
            "X-Kofad-Timestamp": stamp,
            "X-Kofad-Signature": signature
          },
          body: raw
        }
      );
      if (!result.ok) await message.forward(fallback);
    } catch (error) {
      // Never log customer content or email addresses.
      await message.forward(fallback);
    }
  }
};
