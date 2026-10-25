# Changelog

What changed in SupoClip for people using the product. Newest dates are first.

Add a line here when a pull request changes something a user can see. Use **New**, **Improved**, **Fixed**, or **Under the hood**, describe the change in plain language, and link the pull request. Skip internal-only work, or fold it into one **Under the hood** line. Do not include customer names, email addresses, user IDs, or anything else private.

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
