package cn.rainclassroom.assistant;

import java.net.MalformedURLException;
import java.net.URL;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/** 提取并保留题干和各选项对应的图片。 */
final class QuestionContent {
    private static final Pattern IMAGE_TAG = Pattern.compile("<img\\b[^>]*>", Pattern.CASE_INSENSITIVE);
    private static final Pattern IMAGE_ATTR = Pattern.compile(
            "(?:^|\\s)(data-src|src)\\s*=\\s*(?:\"([^\"]*)\"|'([^']*)'|([^\\s>]+))",
            Pattern.CASE_INSENSITIVE);

    static final class Part {
        final String html;
        final Set<String> images;

        Part(String html, Set<String> images) {
            this.html = html;
            this.images = images;
        }
    }

    final Part stem;
    final Map<String, Part> options = new LinkedHashMap<>();

    QuestionContent(JSONObject question) {
        stem = readPart(question, "body", "content", "title");
        JSONArray choices = question.optJSONArray("options");
        if (choices == null) return;
        for (int i = 0; i < choices.length(); i++) {
            JSONObject option = choices.optJSONObject(i);
            String key = option == null ? "" : option.optString("key", "").trim().toUpperCase(Locale.ROOT);
            if (key.isEmpty()) key = String.valueOf((char) ('A' + i));
            options.put(key, option == null
                    ? readPart(choices.optString(i, ""))
                    : readPart(option, "value", "content"));
        }
    }

    Map<String, Set<String>> imageReferences() {
        Map<String, Set<String>> references = new LinkedHashMap<>();
        if (!stem.images.isEmpty()) references.put("题干图片", stem.images);
        for (Map.Entry<String, Part> option : options.entrySet()) {
            if (!option.getValue().images.isEmpty()) {
                references.put("选项" + option.getKey() + "图片", option.getValue().images);
            }
        }
        return references;
    }

    static JSONObject merge(JSONObject cache, JSONObject pushed) {
        if (cache == null) cache = new JSONObject();
        if (pushed == null) pushed = new JSONObject();
        QuestionContent older = new QuestionContent(cache);
        QuestionContent newer = new QuestionContent(pushed);
        Part stem = mergePart(older.stem, newer.stem);
        LinkedHashMap<String, Part> choices = new LinkedHashMap<>(older.options);
        newer.options.forEach((key, part) -> choices.merge(key, part, QuestionContent::mergePart));
        JSONArray options = new JSONArray();
        JSONObject merged = new JSONObject();
        try {
            Object type = firstPresent(pushed.opt("problemType"), cache.opt("problemType"),
                    pushed.opt("type"), cache.opt("type"));
            if (type != null && type != JSONObject.NULL) merged.put("problemType", type);
            merged.put("body", stem.html).put("images", new JSONArray(stem.images));
            for (Map.Entry<String, Part> option : choices.entrySet()) {
                Part part = option.getValue();
                options.put(new JSONObject().put("key", option.getKey()).put("value", part.html)
                        .put("images", new JSONArray(part.images)));
            }
            merged.put("options", options);
            JSONArray blanks = pushed.optJSONArray("blanks");
            if (blanks == null || blanks.length() == 0) blanks = cache.optJSONArray("blanks");
            if (blanks != null) merged.put("blanks", blanks);
            if (cache.optBoolean("_identity_missing") || pushed.optBoolean("_identity_missing")) {
                merged.put("_identity_missing", true);
            }
        } catch (JSONException e) {
            throw new IllegalArgumentException("题面格式异常", e);
        }
        return merged;
    }

    static boolean hasContent(JSONObject question) {
        return !question.optString("body", "").trim().isEmpty() || !question.optString("content", "").trim().isEmpty()
                || !question.optString("title", "").trim().isEmpty() || hasEntries(question, "options")
                || hasEntries(question, "images");
    }

    static boolean hasOptions(JSONObject question) {
        return hasEntries(question, "options");
    }

    private static boolean hasEntries(JSONObject question, String key) {
        JSONArray values = question.optJSONArray(key);
        return values != null && values.length() > 0;
    }

    private static Object firstPresent(Object... values) {
        for (Object value : values) {
            if (value != null && value != JSONObject.NULL && !"".equals(value)) return value;
        }
        return null;
    }

    private static Part readPart(JSONObject part, String... fields) {
        String html = "";
        Set<String> images = new LinkedHashSet<>();
        for (String field : fields) {
            String value = part.optString(field, "");
            if (part.isNull(field) || value.trim().isEmpty()) continue;
            if (html.isEmpty() || (!hasText(html) && hasText(value))) html = value;
            collectHtmlImages(value, images);
        }
        collectImages(part.opt("images"), images);
        removeResolvedMarker(images);
        return new Part(html, images);
    }

    private static Part readPart(String html) {
        Set<String> images = new LinkedHashSet<>();
        collectHtmlImages(html, images);
        removeResolvedMarker(images);
        return new Part(html, images);
    }

    private static Part mergePart(Part older, Part newer) {
        String html = hasText(newer.html) || older.html.isEmpty() ? newer.html : older.html;
        Set<String> images = new LinkedHashSet<>(older.images);
        images.addAll(newer.images);
        removeResolvedMarker(images);
        return new Part(html, images);
    }

    private static boolean hasText(String html) {
        return !html.replaceAll("<[^>]*>", "").replace("&nbsp;", " ").trim().isEmpty();
    }

    private static void collectHtmlImages(String html, Set<String> images) {
        Matcher tags = IMAGE_TAG.matcher(html);
        while (tags.find()) {
            Matcher attrs = IMAGE_ATTR.matcher(tags.group());
            String src = "", dataSrc = "";
            while (attrs.find()) {
                String value = attrs.group(2) != null ? attrs.group(2) : attrs.group(3) != null ? attrs.group(3) : attrs.group(4);
                if ("data-src".equalsIgnoreCase(attrs.group(1))) dataSrc = value.trim();
                else src = value;
            }
            images.add(normalize(dataSrc.isEmpty() ? src : dataSrc));
        }
    }

    private static void collectImages(Object value, Set<String> images) {
        if (value == null || value == JSONObject.NULL) return;
        if (value instanceof JSONArray) {
            JSONArray list = (JSONArray) value;
            for (int i = 0; i < list.length(); i++) collectImages(list.opt(i), images);
        } else if (value instanceof JSONObject) {
            JSONObject object = (JSONObject) value;
            for (String key : new String[]{"url", "src", "data-src"}) {
                Object candidate = object.opt(key);
                if (candidate instanceof String && !((String) candidate).trim().isEmpty()) {
                    images.add(normalize((String) candidate));
                    return;
                }
            }
            images.add("");
        } else if (value instanceof String) {
            images.add(normalize((String) value));
        }
    }

    private static void removeResolvedMarker(Set<String> images) {
        if (images.size() > 1) images.remove("");
    }

    private static String normalize(String source) {
        String url = source.trim().replace("&amp;", "&");
        if (url.isEmpty() || url.startsWith("data:image/")) return url;
        try {
            return new URL(new URL("https://www.yuketang.cn/"), url).toString();
        } catch (MalformedURLException ignored) {
            return url;
        }
    }
}
