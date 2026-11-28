
const express = require("express");
const { z } = require("zod");

const {
  getSettings,
  saveSettings
} = require("../services/dynamodb");

const { publishSettings } = require("../services/mqtt");

const router = express.Router();

const defaults = {
  posture_enabled: true,
  posture_bad_after_s: 10,
  posture_status_every_s: 5,
  head_auto_home_s: 30,
  voice_enabled: true,
  wakeword: "hey_jarvis",
  wake_threshold: 0.5,
  tts_voice: "th-TH-PremwadeeNeural",
  battery_every_s: 30,
  battery_low_pct: 20,
  battery_very_low_pct: 10
};

const settingsSchema = z.object({
  posture_enabled: z.boolean().optional(),
  posture_bad_after_s: z.number().int().min(1).max(120).optional(),
  posture_status_every_s: z.number().int().min(1).max(60).optional(),
  head_auto_home_s: z.number().int().min(1).max(300).optional(),
  voice_enabled: z.boolean().optional(),
  wakeword: z.string().min(1).max(100).optional(),
  wake_threshold: z.number().min(0).max(1).optional(),
  tts_voice: z.enum([
    "th-TH-PremwadeeNeural",
    "th-TH-NiwatNeural"
  ]).optional(),
  battery_every_s: z.number().int().min(5).max(3600).optional(),
  battery_low_pct: z.number().int().min(1).max(100).optional(),
  battery_very_low_pct: z.number().int().min(1).max(100).optional()
}).strict();

router.get("/", async (req, res, next) => {
  try {
    const item = await getSettings();

    res.json({
      device_id: "beggy-pi",
      settings: {
        ...defaults,
        ...(item?.settings || {})
      },
      updated_at: item?.updated_at || null
    });
  } catch (error) {
    next(error);
  }
});

router.patch("/", async (req, res, next) => {
  try {
    const parsed = settingsSchema.safeParse(req.body);

    if (!parsed.success) {
      return res.status(400).json({
        error: "Invalid settings",
        details: parsed.error.issues
      });
    }

    const patch = parsed.data;

    if (Object.keys(patch).length === 0) {
      return res.status(400).json({
        error: "No settings provided"
      });
    }

    const current = await getSettings();

    const updated = {
      ...defaults,
      ...(current?.settings || {}),
      ...patch
    };

    await saveSettings("beggy-pi", updated);

    await publishSettings(patch);

    res.status(202).json({
      message: "Settings saved and published",
      settings: updated,
      published: patch
    });
  } catch (error) {
    next(error);
  }
});

module.exports = router;
