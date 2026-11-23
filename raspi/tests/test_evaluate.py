from posture.evaluate import markdown_report, predicted_class, score


def test_predicted_class():
    assert predicted_class(False, []) == "away"
    assert predicted_class(True, []) == "good"
    # asked issue among several -> counts as that issue
    assert predicted_class(True, ["slouch", "head_down"], "head_down") == "head_down"
    # asked something else -> filed under the highest-priority issue
    assert predicted_class(True, ["slouch", "too_close"], "good") == "too_close"


def test_score_counts():
    rows = ([{"truth": "good", "pred": "good"}] * 8 + [{"truth": "good", "pred": "slouch"}] * 2
            + [{"truth": "slouch", "pred": "slouch"}] * 6 + [{"truth": "slouch", "pred": "too_close"}] * 2
            + [{"truth": "slouch", "pred": "good"}] * 2 + [{"truth": "away", "pred": "away"}] * 5)
    s = score(rows)
    assert s["frames"] == 25
    assert s["accuracy"] == round(19 / 25, 3)
    g = s["good_vs_bad"]
    # slouch->too_close is still "bad": the alert would be right
    assert (g["tp"], g["tn"], g["fp"], g["fn"]) == (8, 8, 2, 2)
    assert g["accuracy"] == 0.8 and g["false_alarm_rate"] == 0.2
    assert s["confusion"]["slouch"]["too_close"] == 2
    assert s["per_class"]["slouch"]["recall"] == 0.6
    md = markdown_report(s, {"infer_ms_mean": 28, "infer_ms_p95": 35, "model_fps": 35.7, "pipeline_fps": 20},
                         {"source": "8", "rounds": 1, "hold_s": 8})
    assert "Confusion matrix" in md and "| **slouch** |" in md
