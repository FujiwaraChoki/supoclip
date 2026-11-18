# Changelog

What changed in SupoClip for people using the product. Newest dates are first.

Add a line here when a pull request changes something a user can see. Use **New**, **Improved**, **Fixed**, or **Under the hood**, describe the change in plain language, and link the pull request. Skip internal-only work, or fold it into one **Under the hood** line. Do not include customer names, email addresses, user IDs, or anything else private.

## 2026-11-18

- **New:** Join the SupoClip Discord community from the homepage, changelog, or site footer to share clips, get editing tips, and vote on feature ideas. ([#437](https://github.com/FujiwaraChoki/supoclip/pull/437))
- **Under the hood:** Every feedback message, bug report, sales question and cancellation reason now also reaches the team's assistant chat, alongside Discord, with the same retries. Admins see it in the webhook list and can switch it off or test it. ([#435](https://github.com/FujiwaraChoki/supoclip/pull/435))
- **Fixed:** Feedback delivery no longer ties up the app during slow webhooks, retries stop reliably, and deleting an account clears saved feedback email addresses atomically. ([#433](https://github.com/FujiwaraChoki/supoclip/pull/433))

## 2026-11-17

- **Under the hood:** Feedback, bug reports, sales questions and cancellation reasons sent from the app are now kept and followed up from one inbox, so none get lost when a chat channel is down. ([#433](https://github.com/FujiwaraChoki/supoclip/pull/433))

- **New:** A guide to clipping GTA 6 on the blog: what clipping campaigns pay, which clips get taken down, and the steps from a stream recording to finished vertical clips. It includes a payout calculator you can adjust to your own numbers. ([#431](https://github.com/FujiwaraChoki/supoclip/pull/431))
- **New:** New accounts get a welcome tour on their first visit. It opens with a short animation, shows how to make a first clip, then goes to each page in turn: Library, Social and Analytics, Agents & API and Settings, and ends back on Create at the link field. It can be skipped at any step. ([#423](https://github.com/FujiwaraChoki/supoclip/pull/423))
- **Improved:** Free accounts see what a plan gets them above the link field, with a short animation of a long video being cut into clips. The plan picker's self-hosting option is now a small link. ([#423](https://github.com/FujiwaraChoki/supoclip/pull/423))
- **Fixed:** Uploads and other requests are less likely to fail because the database ran out of connections. A server that has gone quiet, such as the previous release while it drains, now gives its unused database connections back after 10 minutes instead of holding them indefinitely. ([#428](https://github.com/FujiwaraChoki/supoclip/pull/428))
- **Fixed:** The clip studio no longer fails to open, or to start an export, when several clips load at the same moment. A request now uses one database connection at a time, the export list stops piling up refreshes behind a slow one, checking an export's file in storage no longer holds a connection, and a briefly busy server says to try again in a few seconds instead of showing a generic error. ([#429](https://github.com/FujiwaraChoki/supoclip/pull/429))
- **Fixed:** Creating a generation no longer fails when old deployments fill the database connection limit. Connection pools are bounded, and deployments require the previous release to be retired before another is staged. ([#427](https://github.com/FujiwaraChoki/supoclip/pull/427))

- **Fixed:** House promo codes in referral links or entered before checkout are recognised again when billing uses multiple payment accounts. Code validation now uses the same payment account as checkout. ([#382](https://github.com/FujiwaraChoki/supoclip/pull/382))
- **Fixed:** Long YouTube downloads save completed sections so a connection failure has less video to download again. Series jobs show a range for the wait, and the task page tells you when a download is taking longer than expected. ([#426](https://github.com/FujiwaraChoki/supoclip/pull/426))

## 2026-11-16

- **Fixed:** Exporting a clip while its clips are being regenerated no longer hangs and then fails. You're asked to try again in a moment, and if the clip was replaced you're told it's gone. ([#419](https://github.com/FujiwaraChoki/supoclip/pull/419))
- **Improved:** Accounts can process two videos at once, reducing waits when several videos are queued. ([#418](https://github.com/FujiwaraChoki/supoclip/pull/418))

- **Under the hood:** Hosted deployments can switch new requests to a ready frontend and API while uploads and downloads on the previous release finish. ([#412](https://github.com/FujiwaraChoki/supoclip/pull/412))

## 2026-11-15

- **Fixed:** Long videos processed with Claude Haiku have more room for their outlines, reducing incomplete outlines in series, topic, recap and trailer runs. ([#396](https://github.com/FujiwaraChoki/supoclip/pull/396))

- **New:** Settings → Agents & API shows how to connect Claude, ChatGPT or Grok: open the assistant's connector page, paste the SupoClip server URL and sign in. No key to copy. Each assistant lists which of its plans support this. Claude Code can sign in the same way. API keys moved into one tidy list with a Create key button; a new key opens with its Cursor and local config filled in. ([#403](https://github.com/FujiwaraChoki/supoclip/pull/403))

## 2026-11-14

- **Improved:** Interview clips are framed shot by shot instead of shrinking the whole video into a strip. When both people are on camera, each gets their own half of the screen, stacked; a close-up of one person becomes a tight crop on their face; on-screen graphics and b-roll are shown in full. The layout changes exactly on the video's own cuts. ([#392](https://github.com/FujiwaraChoki/supoclip/pull/392))
- **Fixed:** Long Twitch and Kick videos download the parts your clips use, instead of the whole stream, and a download that is still receiving data is no longer cut off after a few minutes. ([#394](https://github.com/FujiwaraChoki/supoclip/pull/394))
- **Fixed:** A subscriber-only or private VOD, and a VOD that is blocked in our regions, is reported as such instead of a generic "try again later". ([#394](https://github.com/FujiwaraChoki/supoclip/pull/394))
- **Fixed:** Age-restricted YouTube videos are now turned down when you submit them, with a note to download the video and upload the file instead. Before, they failed after a long wait with a message to retry, which couldn't work. No minutes are used. ([#391](https://github.com/FujiwaraChoki/supoclip/pull/391))

## 2026-11-12

- **Fixed:** Opening several clips in the studio at once no longer leaves one of them shorter than the clip. If the whole source can't be fetched, the studio says so instead of opening part of it. A clip that already opened short shows the problem and offers to rebuild its editing copy; your earlier draft stays downloadable. ([#379](https://github.com/FujiwaraChoki/supoclip/pull/379))
- **Fixed:** The studio opens a clip with the framing it was generated with, including face tracking and the blurred full-frame fit, instead of a centred crop. ([#379](https://github.com/FujiwaraChoki/supoclip/pull/379))
- **Fixed:** The studio no longer fills your browser's storage. It keeps a small copy of unsaved changes and a short undo history for your last few clips, and removes a generation's copies when you delete it. If storage does fill up, the message explains that your edits still save to your account, and ⋯ → Browser backups shows what is stored and frees what's already saved. ([#379](https://github.com/FujiwaraChoki/supoclip/pull/379))
- **Fixed:** When you queue several videos, they now start in the order you submitted them, and the next one starts as soon as the previous one finishes. Before, a video submitted earlier could keep waiting while later ones went ahead. ([#378](https://github.com/FujiwaraChoki/supoclip/pull/378))
- **Under the hood:** Media playback, downloads and uploads can prefer the independent storage replica to reduce transfer costs, while keeping the other provider as a backup. ([#376](https://github.com/FujiwaraChoki/supoclip/pull/376))

- **Improved:** When you connect claude.ai, ChatGPT or another app and your email isn't verified yet, the approval page sends the verification email itself, and the link brings you straight back to approve. Connection requests now stay open for 30 minutes. ([#375](https://github.com/FujiwaraChoki/supoclip/pull/375))
- **Under the hood:** We now measure the main steps from sign-up to a first video, a subscription and later renewals. Only the kind of step is recorded, never your links, files, instructions or captions. ([#370](https://github.com/FujiwaraChoki/supoclip/pull/370))
- **New:** Connect SupoClip to claude.ai, ChatGPT and other apps with custom connectors: add `https://mcp.supoclip.com/mcp`, sign in, and allow access. No key to copy. Each connection gets its own API key, listed in Settings → Agents & API, where you can revoke it to disconnect. ([#372](https://github.com/FujiwaraChoki/supoclip/pull/372))
- **Fixed:** The time left on a generation now includes downloading the chosen moments and rendering each clip, so it no longer says "less than a minute" while several minutes remain. ([#371](https://github.com/FujiwaraChoki/supoclip/pull/371))
- **Fixed:** The SupoClip MCP server at `mcp.supoclip.com` now works with clients that use the current MCP connection type, as well as older SSE clients, including ones already set up with the old address. When a connection fails, the error says what is missing, such as an API key or a placeholder that wasn't replaced. ([#368](https://github.com/FujiwaraChoki/supoclip/pull/368))
- **Improved:** Settings → Agents & API shows ready-to-copy setup for Claude Code, Cursor, Claude Desktop and a local server, with your new key filled in. ([#368](https://github.com/FujiwaraChoki/supoclip/pull/368))
- **Improved:** When an agent tries to make clips on an account without a paid plan or without minutes left, it gets a plain explanation and a link to choose a plan instead of a raw error. ([#368](https://github.com/FujiwaraChoki/supoclip/pull/368))
- **Under the hood:** Billing can keep existing subscriptions with their current payment account while preparing a new account, with separate payment recovery and refund handling. ([#367](https://github.com/FujiwaraChoki/supoclip/pull/367))

## 2026-11-10

- **Fixed:** With reduced motion enabled, the homepage and sign-in and sign-up forms now keep their contents as the page becomes interactive. ([#358](https://github.com/FujiwaraChoki/supoclip/pull/358))

- **Improved:** A studio export or clip preparation whose server stops unexpectedly is now retried or shown as failed within a few minutes, instead of staying in progress. ([#359](https://github.com/FujiwaraChoki/supoclip/pull/359))
- **Under the hood:** Video rendering can run on additional servers. Studio drafts and exports are kept in the database, and uploads, fonts and finished videos are shared through storage instead of one server's disk. ([#359](https://github.com/FujiwaraChoki/supoclip/pull/359))
- **Fixed:** When we can't verify a YouTube, Twitch or Kick video's length, the message explains that the stream may still be live and suggests using the completed recording. For YouTube, we check a second source for the length. If that check confirms a region restriction, we suggest uploading the video file. ([#356](https://github.com/FujiwaraChoki/supoclip/pull/356))
- **Fixed:** Submitting a video that is already queued or processing opens that generation instead of starting a second one. ([#354](https://github.com/FujiwaraChoki/supoclip/pull/354))
- **Under the hood:** Each account renders one video at a time by default, maintenance jobs run on their own worker, and a long queue wait is reported to error tracking. ([#354](https://github.com/FujiwaraChoki/supoclip/pull/354))

## 2026-11-08

- **Fixed:** Retrying a generation after a dropped connection no longer creates and charges a second one, and a video upload whose final step lost its answer no longer has to be sent again. ([#349](https://github.com/FujiwaraChoki/supoclip/pull/349))
- **Fixed:** Uploading a video from the iPhone app no longer fails when sending the file takes longer than five minutes. ([#348](https://github.com/FujiwaraChoki/supoclip/pull/348))

## 2026-11-07

- **Fixed:** TikTok publishing is paused for now, with a short note in Social settings, while an issue with TikTok is fixed. ([#343](https://github.com/FujiwaraChoki/supoclip/pull/343))

## 2026-11-06

- **Fixed:** When YouTube audio comes through the backup download route, the video's length is read correctly, so clips from its last minutes are no longer dropped. ([#333](https://github.com/FujiwaraChoki/supoclip/pull/333))
- **Under the hood:** New features can be switched on for the web and iPhone apps separately, without an update. ([#340](https://github.com/FujiwaraChoki/supoclip/pull/340))

## 2026-11-05

- **Fixed:** The homepage shows TikTok publishing and scheduling as available alongside YouTube, with updated pricing details and FAQ answers. Instagram remains marked as coming soon. ([#339](https://github.com/FujiwaraChoki/supoclip/pull/339))
- **Improved:** Generations show a short, varied notice when a video takes longer than usual. The notice survives a page refresh, replaces the time estimate during delays, and clears when processing continues. ([#336](https://github.com/FujiwaraChoki/supoclip/pull/336))
- **Improved:** Pro includes 10 AI edits per month and Creator includes 100. The editor shows the remaining allowance and reset date, and failed requests restore the edit. ([#335](https://github.com/FujiwaraChoki/supoclip/pull/335))
- **New:** A dedicated FAQ lists every supported spoken language and regional variant, with search and answers about language detection, captions, translation limits, and video processing. It is available in all six interface languages and linked from the homepage and footer. ([#334](https://github.com/FujiwaraChoki/supoclip/pull/334))
- **Under the hood:** Preview deployments use an upload timeout within the hosting platform's supported limit. ([#334](https://github.com/FujiwaraChoki/supoclip/pull/334))
- **Fixed:** Clips stitched together after cutting pauses no longer risk a frozen picture while the audio keeps playing. ([#333](https://github.com/FujiwaraChoki/supoclip/pull/333))
- **Under the hood:** Groundwork for new ways to turn a video into clips, not yet available to everyone. ([#333](https://github.com/FujiwaraChoki/supoclip/pull/333))
- **Improved:** The create screen keeps length, captions, framing, cleanup, and B-roll in one Settings menu, so the composer stays on one line. ([#337](https://github.com/FujiwaraChoki/supoclip/pull/337))
- **Improved:** The create screen remembers your captions, framing, cleanup, B-roll, length, and other settings on your account, and Settings has a one-click reset. Topics, instructions, and part counts still start fresh for each video. ([#337](https://github.com/FujiwaraChoki/supoclip/pull/337))
- **New:** A short, skippable tour walks you through new features once, on the real page, so you can find them without reading release notes. ([#337](https://github.com/FujiwaraChoki/supoclip/pull/337))
- **Fixed:** Long Chinese, Japanese, and Korean titles stay inside the frame instead of running off both edges. ([#337](https://github.com/FujiwaraChoki/supoclip/pull/337))
- **Fixed:** Clips no longer carry chapter markers or extra data tracks from an uploaded MKV file. ([#337](https://github.com/FujiwaraChoki/supoclip/pull/337))
- **Fixed:** Failed generations no longer show file paths or technical processing errors. ([#337](https://github.com/FujiwaraChoki/supoclip/pull/337))
- **Under the hood:** Updated sign-in, video-processing, and developer tools to address published security vulnerabilities. ([#338](https://github.com/FujiwaraChoki/supoclip/pull/338))

## 2026-11-03

- **Fixed:** YouTube videos blocked in the downloader's region can retry from a supported region while keeping saved transcripts and other download options available. Generation and editing explain any remaining regional restriction and suggest uploading the file or choosing another source. ([#332](https://github.com/FujiwaraChoki/supoclip/pull/332))
- **Fixed:** Retrying a failed generation keeps completed clips and your edits, reuses finished transcription and analysis, and generates only the missing clips. Temporary YouTube download failures get a limited automatic retry before falling back. ([#329](https://github.com/FujiwaraChoki/supoclip/pull/329))
- **Improved:** The recovery action is now called “Retry generation” instead of “Resume.” ([#329](https://github.com/FujiwaraChoki/supoclip/pull/329))

## 2026-10-31

- **New:** Download all of a generation's clips at once as a ZIP from the task page. The download starts right away and streams the original files. ([#321](https://github.com/FujiwaraChoki/supoclip/pull/321))
- **Improved:** The footer, terms, and privacy policy now identify the company operating SupoClip. ([#314](https://github.com/FujiwaraChoki/supoclip/pull/314))
- **New:** Switch between a light and dark theme from the sidebar (or the header on mobile). The app starts light and remembers your choice; the website and sign-in pages stay light. ([#312](https://github.com/FujiwaraChoki/supoclip/pull/312))

## 2026-10-29

- **Fixed:** A large video upload finishes once the file is stored, instead of waiting on a copy that can time out. Uploading the same file again reuses it. ([#306](https://github.com/FujiwaraChoki/supoclip/pull/306))

## 2026-10-28

- **Fixed:** When a YouTube download has to fall back to HLS audio, clips stay in the video's original language instead of an automatic dub or an audio-description track. ([#303](https://github.com/FujiwaraChoki/supoclip/pull/303))

## 2026-10-25

- **Fixed:** Deleting a generation no longer restores used video minutes, and every new video has its length verified before minutes are charged. ([#261](https://github.com/FujiwaraChoki/supoclip/pull/261))
- **Fixed:** Upgrading from Pro to Creator takes effect after the upgrade payment succeeds. If that payment is still open or does not go through, you stay on the plan you already paid for. ([#260](https://github.com/FujiwaraChoki/supoclip/pull/260))
- **Fixed:** Downloading a clip in a social format no longer waits on the whole re-encode, so a long export can finish instead of timing out. ([#258](https://github.com/FujiwaraChoki/supoclip/pull/258))
- **Fixed:** Copying an API key, a creator code, or a share link still works when the browser blocks the clipboard. If it cannot copy, the text is selected and a message tells you to copy it yourself. ([#257](https://github.com/FujiwaraChoki/supoclip/pull/257))
- **Under the hood:** A brief server error no longer clears the YouTube download notice or the What's new list, and those checks are not reported as crashes. ([#257](https://github.com/FujiwaraChoki/supoclip/pull/257))

## 2026-10-24

- **Fixed:** A YouTube video whose direct audio link is blocked is retried over HLS before a paid download. ([#250](https://github.com/FujiwaraChoki/supoclip/pull/250))
- **New:** The site is available in Bahasa Indonesia. ([#243](https://github.com/FujiwaraChoki/supoclip/pull/243))
- **Fixed:** Phone videos recorded in portrait no longer fail to render. ([#241](https://github.com/FujiwaraChoki/supoclip/pull/241))
- **Fixed:** Editing a clip keeps a split-screen or stacked layout instead of flattening it back to a single frame. ([#238](https://github.com/FujiwaraChoki/supoclip/pull/238))

## 2026-10-23

- **Improved:** Uploads can be as large as 4 GB. Video above 1080p or 60 fps is scaled down automatically before clipping. ([#220](https://github.com/FujiwaraChoki/supoclip/pull/220))
- **Improved:** Automatic vertical framing keeps more of the scene around the speaker, instead of cropping in on the face alone. ([#236](https://github.com/FujiwaraChoki/supoclip/pull/236))
- **Improved:** Clip analysis on Claude Sonnet uses the model's structured output, so a long video is more likely to come back with a full set of clips. ([ddbc537](https://github.com/FujiwaraChoki/supoclip/commit/ddbc53715c15fdba25d33f6ec4dfc45943b96476))
- **Under the hood:** When you submit a video link, a sanitized copy is written to the logs, including when the job is rejected. ([#235](https://github.com/FujiwaraChoki/supoclip/pull/235))

## 2026-10-22

- **New:** Reaction videos and two people talking over each other can be stacked in the vertical frame. ([#224](https://github.com/FujiwaraChoki/supoclip/pull/224))

## 2026-10-21

- **New:** Hosted plans are billed by hours of source video. Pro is $9.99/month for 10 hours at 720p, Creator is $24.99/month for 25 hours at 1080p, and yearly billing is two months free. Pro subscribers from before this change keep 1080p. ([#216](https://github.com/FujiwaraChoki/supoclip/pull/216))
- **Improved:** Pricing cards spell out how much video a plan includes, and the price stays inside the card. ([#225](https://github.com/FujiwaraChoki/supoclip/pull/225))
- **New:** Paste a Twitch or Kick link — a VOD or a clip — and those sources show up alongside YouTube. ([#227](https://github.com/FujiwaraChoki/supoclip/pull/227), [#228](https://github.com/FujiwaraChoki/supoclip/pull/228))
- **Improved:** Calls and talking-head videos are framed more carefully for a vertical crop. ([#217](https://github.com/FujiwaraChoki/supoclip/pull/217))
- **Fixed:** A clip that rewinds to set up the hook stays within 60 seconds, and a long hook title fits on screen. ([#221](https://github.com/FujiwaraChoki/supoclip/pull/221))
- **New:** The site and app are available in English, Spanish, Portuguese, German, and Chinese. ([#211](https://github.com/FujiwaraChoki/supoclip/pull/211))
- **Under the hood:** YouTube downloads hold up better when several jobs run at once, including through a residential proxy when one is configured. ([#218](https://github.com/FujiwaraChoki/supoclip/pull/218), [#223](https://github.com/FujiwaraChoki/supoclip/pull/223))

## 2026-10-20

- **Fixed:** After you edit a clip, the caption animation and the hook title from the original render stay in place. ([#210](https://github.com/FujiwaraChoki/supoclip/pull/210))

## 2026-10-18

- **Fixed:** The editor keeps karaoke captions from the generated clip, including the font, size, and position. ([#200](https://github.com/FujiwaraChoki/supoclip/pull/200))

## 2026-10-17

- **Under the hood:** YouTube downloads go through yt-dlp or Apify. ([#198](https://github.com/FujiwaraChoki/supoclip/pull/198))

## 2026-10-16

- **New:** Connect YouTube, TikTok, or Instagram to publish a clip and see how those posts perform. ([#98](https://github.com/FujiwaraChoki/supoclip/pull/98))
- **Improved:** The library groups each generation with its original video, and an uploaded file keeps its filename. ([#197](https://github.com/FujiwaraChoki/supoclip/pull/197))
- **New:** A self-hosted install can use its own YouTube download service. ([#196](https://github.com/FujiwaraChoki/supoclip/pull/196))
- **Under the hood:** If the first YouTube download does not check out, SupoClip can try another one within the same limit. ([#195](https://github.com/FujiwaraChoki/supoclip/pull/195))

## 2026-10-15

- **Under the hood:** Clips and uploads are stored durably and removed when you delete them. ([#186](https://github.com/FujiwaraChoki/supoclip/pull/186))

## 2026-10-14

- **Improved:** The generation page shows while a video is still importing, then clears that note once it is ready. ([#189](https://github.com/FujiwaraChoki/supoclip/pull/189), [#190](https://github.com/FujiwaraChoki/supoclip/pull/190))

## 2026-10-13

- **New:** A public status page shows whether SupoClip is up. ([0aab079](https://github.com/FujiwaraChoki/supoclip/commit/0aab079))
- **Fixed:** Closing a video stops the transfer instead of leaving it running. ([#187](https://github.com/FujiwaraChoki/supoclip/pull/187))

## 2026-10-12

- **Fixed:** Caption templates use the real TikTok Sans Bold and THEBOLDFONT, and oversized caption styles are smaller. ([#185](https://github.com/FujiwaraChoki/supoclip/pull/185))

## 2026-10-11

- **New:** Clips open on a short hook, then cut to the sentence where the moment actually starts. ([#174](https://github.com/FujiwaraChoki/supoclip/pull/174))
- **Improved:** Long pauses are tightened so a clip keeps moving. ([#173](https://github.com/FujiwaraChoki/supoclip/pull/173))
- **Improved:** On a wide shot with several people, the crop follows whoever is speaking. ([#175](https://github.com/FujiwaraChoki/supoclip/pull/175))
- **Improved:** A long video is reviewed in sections, then the best moments are ranked together. ([#176](https://github.com/FujiwaraChoki/supoclip/pull/176))
- **Fixed:** Face tracking works again, so a vertical crop can stay on the person. ([#177](https://github.com/FujiwaraChoki/supoclip/pull/177))
- **Improved:** Upscaled vertical crops look sharper. ([#172](https://github.com/FujiwaraChoki/supoclip/pull/172))
- **Improved:** Each video produces at most 10 clips. ([#181](https://github.com/FujiwaraChoki/supoclip/pull/181))
- **New:** The home page and the demo play a new set of podcast clips. ([#182](https://github.com/FujiwaraChoki/supoclip/pull/182))
- **New:** Checkout accepts a creator promo code. ([#183](https://github.com/FujiwaraChoki/supoclip/pull/183))
- **New:** The app can show an announcement from the SupoClip team. ([#179](https://github.com/FujiwaraChoki/supoclip/pull/179))
- **Improved:** The home page includes the Product Hunt badge. ([#184](https://github.com/FujiwaraChoki/supoclip/pull/184))
- **Fixed:** If every clip fails to render, the job is marked failed instead of looking finished. A job that never starts does not use your monthly video allowance. ([#169](https://github.com/FujiwaraChoki/supoclip/pull/169), [#170](https://github.com/FujiwaraChoki/supoclip/pull/170), [#171](https://github.com/FujiwaraChoki/supoclip/pull/171))

## 2026-10-10

- **New:** The creator program is open. Apply, share your code, and your audience gets 20% off for 3 months. Approved creators get Pro. ([#165](https://github.com/FujiwaraChoki/supoclip/pull/165))
- **Improved:** Pricing explains that self-hosting is free, next to the hosted plans. ([#166](https://github.com/FujiwaraChoki/supoclip/pull/166))
- **Improved:** Affiliate is linked from the top of the site and the footer. ([#168](https://github.com/FujiwaraChoki/supoclip/pull/168))

## 2026-10-06

- **Improved:** Captions detect the spoken language, stay lined up with the audio, and use fonts that cover more languages. ([#163](https://github.com/FujiwaraChoki/supoclip/pull/163))

## 2026-10-05

- **New:** A demo page and practical guides cover clipping, self-hosting, and how SupoClip compares with other tools. ([#160](https://github.com/FujiwaraChoki/supoclip/pull/160))

## 2026-10-04

- **Improved:** Upgrade prompts explain what the next plan adds, and you can move from Pro to Creator without starting a new subscription. ([#159](https://github.com/FujiwaraChoki/supoclip/pull/159))

## 2026-10-03

- **New:** You can delete your account from Settings. ([#158](https://github.com/FujiwaraChoki/supoclip/pull/158))
- **New:** If you forget your password, you can reset it by email. ([#157](https://github.com/FujiwaraChoki/supoclip/pull/157))

## 2026-10-02

- **Fixed:** A transcription that stalls gives up instead of waiting forever, and the app can tell when the worker is down. ([#155](https://github.com/FujiwaraChoki/supoclip/pull/155))
- **Under the hood:** Self-hosted installs can build a CPU-only image and keep the sign-in addresses they already use. ([#153](https://github.com/FujiwaraChoki/supoclip/pull/153), [#154](https://github.com/FujiwaraChoki/supoclip/pull/154))
