package cn.rainclassroom.assistant;

import android.os.SystemClock;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/** 存在当前进程内的待核对答案；所有结束动作互斥。 */
public final class ReviewRegistry {
    public interface Completion { void finished(Review review, boolean cancelled); }
    public static final class Review {
        public final String id, course, problemId;
        public final int type;
        public final JSONObject problem;
        public volatile JSONArray answers;
        public final long deadlineElapsed, questionDeadlineElapsed;
        private final Completion completion;
        private boolean finished;
        private Review(String course, String problemId, int type, JSONObject problem, JSONArray answers,
                       long deadlineElapsed, long questionDeadlineElapsed, Completion completion) {
            this.id = UUID.randomUUID().toString(); this.course = course; this.problemId = problemId;
            this.type = type; this.problem = problem; this.answers = copy(answers);
            this.deadlineElapsed = deadlineElapsed; this.questionDeadlineElapsed = questionDeadlineElapsed;
            this.completion = completion;
        }
    }
    private static final ConcurrentHashMap<String, Review> REVIEWS = new ConcurrentHashMap<>();
    private ReviewRegistry() {}
    static JSONArray copy(JSONArray source) {
        try { return new JSONArray(source.toString()); } catch (JSONException e) { throw new IllegalArgumentException(e); }
    }
    public static Review create(String course, String problemId, int type, JSONObject problem, JSONArray answers,
                                long deadlineElapsed, long questionDeadlineElapsed, Completion completion) {
        if (!validForProblem(type, problem, answers)) throw new IllegalArgumentException("答案为空或格式有误");
        Review r = new Review(course, problemId, type, problem, answers, deadlineElapsed, questionDeadlineElapsed, completion);
        REVIEWS.put(r.id, r); return r;
    }
    public static List<Review> list() { return new ArrayList<>(REVIEWS.values()); }
    public static Review get(String id) { return id == null ? null : REVIEWS.get(id); }
    public static boolean valid(int type, JSONArray answers) {
        if (answers == null || answers.length() == 0 || (type == 1 && answers.length() != 1) || (type == 5 && answers.length() != 1)) return false;
        for (int i = 0; i < answers.length(); i++) {
            Object answer = answers.opt(i);
            if (!(answer instanceof String) || ((String) answer).trim().isEmpty()) return false;
        }
        return true;
    }
    private static boolean validForProblem(int type, JSONObject problem, JSONArray answers) {
        if (!valid(type, answers)) return false;
        if (type == 1 || type == 2) {
            JSONArray options = problem.optJSONArray("options");
            if (options == null || options.length() == 0) return false;
            java.util.Set<String> allowed = new java.util.HashSet<>();
            for (int i=0; i<options.length(); i++) {
                JSONObject opt = options.optJSONObject(i);
                String key = opt == null ? String.valueOf((char)('A'+i)) : opt.optString("key", String.valueOf((char)('A'+i)));
                allowed.add(key.toUpperCase(java.util.Locale.ROOT));
            }
            java.util.Set<String> used = new java.util.HashSet<>();
            for (int i=0; i<answers.length(); i++) {
                String answer = answers.optString(i).trim().toUpperCase(java.util.Locale.ROOT);
                if (!allowed.contains(answer) || !used.add(answer)) return false;
            }
        } else if (type == 4) {
            JSONArray blanks = problem.optJSONArray("blanks");
            if (blanks != null && blanks.length() > 0 && answers.length() != blanks.length()) return false;
        }
        return true;
    }
    public static boolean update(String id, JSONArray answers) {
        Review r = get(id); if (r == null || !validForProblem(r.type, r.problem, answers)) return false;
        synchronized (r) {
            if (r.finished || SystemClock.elapsedRealtime() >= r.deadlineElapsed) return false;
            r.answers = copy(answers); return true;
        }
    }
    public static boolean submit(String id) { return finish(id, false); }
    public static boolean cancel(String id) { return finish(id, true); }
    private static boolean finish(String id, boolean cancelled) {
        Review r = get(id); if (r == null) return false;
        synchronized (r) {
            if (r.finished) return false;
            r.finished = true; REVIEWS.remove(r.id, r);
        }
        r.completion.finished(r, cancelled); return true;
    }
    public static void cancelAll() { for (Review r : list()) cancel(r.id); }
}
