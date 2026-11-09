# Setting up a test account for the Chrome Web Store reviewers

A step-by-step list. Do the steps in order and tick each box. Each step says what you
should see, so you know it worked before moving on.

> **Status:** puzzle-chat 1.0.0 was submitted (Unlisted) on 2026-11-08 using this setup.
> Store Item ID: **`jadkkcgfeaadaobdmcgcafikblnkiand`**. For a later update, most of this is
> already in place: re-check Parts F and G, upload the new zip, and submit.

**Why we need this:** the extension does nothing until you're logged in to a Puzzbot
dashboard, and you log in to the dashboard with Discord. A Google reviewer has no Discord
account on our server, so we lend them one: a throwaway Discord account, sitting in a test
server, with a test hunt they can see. The reviewer uses the **test** dashboard
(https://puzzbot.com), never live.

**Time needed:** about an hour, mostly waiting for emails.

**The traps this list avoids:**

1. **Discord's "new login location" email.** When the reviewer logs in from Google's
   office, Discord emails a "was this you?" link before letting them in. If they can't
   read that email, they're stuck and the review fails. So we give them a mailbox too
   (Part A).
2. **The Store copy has a different ID.** The copy of the extension you've been testing
   has one ID (`okcicdkjcbhomokginfnnefllmkjejbi`). The copy the Store hands out has
   another (`jadkkcgfeaadaobdmcgcafikblnkiand`). The dashboard only talks to IDs on its
   list, so the Store ID must go on that list (Part E). Without it, the chat panel never
   goes live.
3. **The extension points at live by default.** The reviewer has to switch it to the test
   dashboard, and the instructions must say so in plain words (Part G).
4. **Save every password the moment you make it.** If a password manager generates one but
   doesn't save it, Bitwarden keeps it under **Generator → history** (clock icon).

---

## Part A: Make a mailbox the reviewer can open

The throwaway Discord account needs an email address that the reviewer can read with
just a password: no phone, no second factor.

puzzbot.com's mail goes to **Cloudflare Email Routing**, which only *forwards*; it has no
inbox anyone can log in to. (A Plesk mailbox would never receive anything, since the MX
records point at Cloudflare.) So the address `review@puzzbot.com` forwards to a free
mailbox, and the reviewer logs in to that.

- [ ] A1. Create a free **Proton Mail** account (https://proton.me), e.g.
  `qd-store-review@proton.me`. Save the password in Bitwarden straight away. Don't add a
  phone number.
- [ ] A2. In Proton: **Settings → Account and password → Recovery**, add **your own** email
  as the recovery email, and click the link Proton sends you.
  **Why:** a brand-new Proton account is blocked from receiving sign-up and verification
  emails from other services ("restricted from registering on third-party services")
  until it has a verified recovery email. It doesn't affect logging in: the reviewer still
  needs only the password.
- [ ] A3. In Cloudflare: puzzbot.com → **Email → Email Routing → Destination addresses** →
  add the Proton address. Open Proton (check **Spam** too) and click Cloudflare's
  verification link.
  **You should see:** the address marked **Verified** in Cloudflare.
- [ ] A4. **Routing rules** → **Create address**: `review@puzzbot.com` → send to the Proton
  address. Make sure it's **Active**.
- [ ] A5. Send a test email from your own account to `review@puzzbot.com`.
  **You should see:** it in Proton within a minute. If not, Email Routing's **Activity
  log** says whether it was forwarded, dropped (no matching rule) or rejected (destination
  not verified).

> Don't use a Gmail account for the mailbox. Google will ask the reviewer for a phone
> number, and that's the same trap again. If Proton stays stuck, Outlook.com, GMX or
> mail.com work as the forwarding destination instead.

## Part B: Make the throwaway Discord account

- [ ] B1. In a **private / incognito window** (so you stay logged in as yourself
  elsewhere), go to https://discord.com/register.
- [ ] B2. Email: `review@puzzbot.com`. Display name: `Store Reviewer`. Username: something
  like `qd-store-review`. Password: a new long random one; save it in Bitwarden. Fill in a
  date of birth that makes the account an adult.
- [ ] B3. Discord sends a "Verify Email Address" email. Open Proton and click it.
  **You should see:** Discord says the email is verified.
- [ ] B4. In that Discord account: **User Settings → My Account**.
  - Check that **Two-Factor Authentication** is **off**. Never turn it on.
  - Don't add a phone number.
- [ ] B5. Stay logged in to this window for now. You'll need it in Part C.

## Part C: Put the account in the test server

Use the Discord server that the **test** bot (puzzbot.com) lives in.

- [ ] C1. As yourself, open that server's **Server Settings → Safety Setup** (or
  **Moderation**) and look at **Verification Level**. If it's **High** or **Highest**,
  the reviewer would need a phone number. Set it to **Low** for the review period.
  (Low only needs a verified email, which we have.)
- [ ] C2. As yourself, make an invite link: right-click the server, **Invite People**,
  then **Edit invite link** → **Expire after: Never**, **Max uses: No limit**. Copy it.
- [ ] C3. In the incognito window (the reviewer account), open the invite and join.
  **You should see:** "Store Reviewer" in the server's member list.

## Part D: Make a review hunt they can see

- [ ] D1. As yourself, in the test server's bot channel, create a small hunt **just for
  the review**, so nobody tidies it away by accident:
  `!hunt Store Review:https://example.com, Store Reviewer Team`
  (The last part makes a role so only that team sees the hunt.)
- [ ] D2. Give the throwaway account that role: right-click **Store Reviewer** in the
  member list → **Roles** → tick **Store Reviewer Team**.
- [ ] D3. In the hunt's channel, make one puzzle: `!p Sample Puzzle`.
  **You should see:** a new `sample-puzzle` channel, and its pinned message has a
  **Google Sheet** link.
- [ ] D4. Post a message or two in `#sample-puzzle` ("Hello reviewer!"), so the chat
  panel has something to show.
- [ ] D5. Copy the **Google Sheet** link from the pinned message. That's the sheet URL
  for Part G. Trim it to `https://docs.google.com/spreadsheets/d/<id>` (cut `/edit` and
  everything after it); it has to fit in a 500-character box.
- [ ] D6. Check the sheet opens for a stranger: paste the link into a **different**
  incognito window where you're **not** signed in to Google.
  **You should see:** the spreadsheet (view-only is fine).
  If instead it asks you to sign in, or says "You need access": open the sheet as
  yourself, click **Share** → **General access** → **Anyone with the link**, set it to
  **Viewer**, then check again.

## Part E: Upload the draft and add the Store ID to test

- [ ] E1. In the Chrome Web Store developer dashboard
  (https://chrome.google.com/webstore/devconsole), create the item (**New item**) or open
  it, and upload the zip built by `extension/package.sh`
  (`extension/dist/puzzle-chat-<version>.zip`). Rebuild it first if any extension code
  changed since it was built.
- [ ] E2. The **Item ID** is shown on the item's page and in its URL. Ours is
  `jadkkcgfeaadaobdmcgcafikblnkiand`; it doesn't change between versions.
- [ ] E3. In Plesk, on the **test** site (puzzbot.com), open `config.json`. Find
  `"extension_origins"` under `"web"`. It's a list. **Add** the Store ID; don't replace
  your dev one:
  ```json
  "extension_origins": [
    "chrome-extension://okcicdkjcbhomokginfnnefllmkjejbi",
    "chrome-extension://jadkkcgfeaadaobdmcgcafikblnkiand"
  ]
  ```
  Mind the commas: one between items, none after the last.
- [ ] E4. Restart the test bot the way you did for live after the last config change.
  **You should see:** https://puzzbot.com loads normally.
- [ ] E5. Do the same on **live** too (quarantinedecrypters.com), adding the same Store ID,
  so teammates who install from the Store can use live. Restart live when convenient.

## Part F: Do the reviewer's steps yourself first

Pretend to be Google. Use a **fresh Chrome profile** (click your profile picture in
Chrome → **Add** → **Continue without an account**), so nothing of yours helps.

- [ ] F1. In the fresh profile, log in to https://discord.com as the throwaway account.
  Discord may email a "new login location" link. Open it in Proton
  (https://mail.proton.me). **This is exactly what the reviewer will hit, so make sure it
  works.**
- [ ] F2. Install your test copy of the extension: `chrome://extensions` → **Developer
  mode** → **Load unpacked**, or drag in the zip. (On a first submission the Store copy
  isn't public yet; this checks everything else.)
- [ ] F3. Right-click the extension's toolbar icon → **Options**. Set **Dashboard origin**
  to `https://puzzbot.com` → **Save** → **Allow** when Chrome asks.
- [ ] F4. Open https://puzzbot.com → **Log in with Discord** → **Authorize**.
  **You should see:** the dashboard, with the **Store Review** hunt listed (plus any
  ungated hunts on the test server; that's fine).
- [ ] F5. Open the sheet link from D5. Click the extension's icon.
  **You should see:** the side panel with "Hello reviewer!" from D4.
- [ ] F6. Type "test from the panel" and press Enter.
  **You should see:** it in the panel and in `#sample-puzzle` in Discord, under Store
  Reviewer's name.
- [ ] F7. On the dashboard, open **Sample Puzzle** → **Work on this puzzle**.
  **You should see:** it jumps to the sheet tab, with the panel open.
- [ ] F8. Close the fresh profile. If any step failed, stop and fix it before Part G.

## Part G: The Store's "Test instructions" tab

The dashboard has a **Test instructions** tab with **Username**, **Password** and
**Additional instructions** boxes. **Additional instructions is limited to 500
characters**, so it has to be terse.

- **Username:** `review@puzzbot.com`
- **Password:** the Discord password from B2
- **Additional instructions:** copy this and replace `X` (Proton address), `Y` (Proton
  password) and `SHEET` (the trimmed link from D5):

```text
Uses the Discord login above. If Discord asks to verify location, read its email at mail.proton.me (user X, pw Y).
1. Extension Options: Dashboard origin = https://puzzbot.com, Save, Allow.
2. Open https://puzzbot.com, Log in with Discord, Authorize.
3. Open SHEET and click the extension icon: the side panel shows the chat.
4. Type a message + Enter: it posts to Discord.
```

Without the three values it's about 367 characters, leaving about 130 for them: a
~25-character Proton address, a password of about 20 characters, and the 83-character
sheet link. The "only reads Sheets tab addresses, no remote code" point belongs in the
Privacy tab's justifications (from `listing.md`), not here.

## Part H: Final check, then submit

- [ ] H1. `X`, `Y` and `SHEET` in Part G are all replaced, and the box accepted the text
  (it refuses more than 500 characters).
- [ ] H2. Store listing: privacy policy URL is `https://quarantinedecrypters.com/privacy`;
  the permission justifications and data-use answers are copied from
  `extension/store/listing.md`.
- [ ] H3. Visibility: **Unlisted**.
- [ ] H4. Press **Submit for review**.

## While the review is running (days, sometimes a couple of weeks)

Leave everything in place. In particular:

- Don't archive or delete the **Store Review** hunt, or kick the throwaway account.
- Don't change the throwaway account's password, or turn on 2FA.
- Keep the invite link, the Cloudflare routing rule and the Proton mailbox alive.
- Be careful with pushes to `staging` (they redeploy puzzbot.com). A broken test site
  during the review means a rejection. If a big risky change is due, wait or check
  https://puzzbot.com straight after.
- Check the Proton mailbox now and then. Google sometimes writes to the developer
  contact address, and Discord security emails land in Proton.

## After it's approved

- Teammates install it from the Store link (unlisted = only people with the link).
- Their **Options → Dashboard origin** is already the live site (the default).
- Keep the review hunt and the throwaway account: each update is reviewed again, and
  they're needed for that.

## If it's rejected

The email names the reason. The usual ones, and what to do:

- **"Could not log in / could not access functionality":** redo Part F in a fresh profile
  and see where it breaks (most often the new-login email, or a sheet that isn't shared).
- **"Excessive permissions":** point at the justifications in `listing.md`; reply via
  the dashboard's appeal link.
- **"Missing privacy policy / data disclosure":** check H2.
