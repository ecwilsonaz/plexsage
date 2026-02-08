# Using Gemini with MediaSage (Free Tier)

Google's Gemini API has a free tier that works well for personal playlist generation. No credit card required.

---

## Setup

1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey) and sign in with any Google account
2. Click **Create API key** and select or create a Google Cloud project
3. Copy the key and add it to your MediaSage config:

```yaml
services:
  mediasage:
    environment:
      - GEMINI_API_KEY=your-api-key-here
```

MediaSage auto-detects Gemini when this key is set.

---

## Free Tier Limits

| Model | Requests/Min | Requests/Day | Free? |
|-------|-------------|--------------|-------|
| **Gemini 2.5 Flash** | ~10–15 | ~100–500 | Yes |
| Gemini 2.5 Pro | ~5 | ~25–50 | Yes |

MediaSage uses **Gemini 2.5 Flash** by default — fast, handles large track lists, and has the most generous free limits. Even at 100 requests/day, that's 100 playlists. Limits reset at midnight PT.

All Gemini models support a 1M token context window. MediaSage sends ~500–650 tracks per request, well within this limit.

---

## Cost

On the free tier: **$0.00**.

On the paid tier, a typical playlist costs $0.03–0.25 with Gemini 2.5 Flash.

---

## Things to Know

**Data usage:** On the free tier, Google may use your prompts to improve their products. Enabling billing (even with $0 spent) opts you out.

**Regional restrictions:** The free tier may not be available from the EU, UK, or Switzerland. Setting up billing works around this.

**Rate limits:** 429 errors mean you've hit your limit. Wait a minute or until the daily reset.

---

## Additional Free Options

**$300 Google Cloud credits** — New Google Cloud users can get $300 in free credits (90 days). In [AI Studio](https://aistudio.google.com), go to Settings → Plan information → Set up Billing. Credit card required but not charged until credits run out. At ~$0.03–0.25/playlist, this covers thousands of playlists.

**Google AI Pro credits** — If you pay for Google AI Pro ($19.99/mo), you get $10/month in Cloud credits via the [Google Developer Program](https://developers.google.com/profile).

---

## Quick Reference

| | |
|---|---|
| Get your key | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| Cost | Free (no credit card) |
| Default model | Gemini 2.5 Flash |
| Playlists/day (free) | ~100–500 |
| Context window | 1M tokens |
| Data privacy (free) | Prompts may be used by Google |
| Data privacy (paid) | Prompts not used by Google |
