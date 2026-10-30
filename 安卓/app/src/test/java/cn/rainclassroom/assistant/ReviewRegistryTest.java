package cn.rainclassroom.assistant;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.After;
import org.junit.Test;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;
import static org.junit.Assert.*;

public class ReviewRegistryTest {
    @After public void cleanup() { ReviewRegistry.cancelAll(); }

    private static JSONObject choiceProblem() throws Exception {
        return new JSONObject().put("options", new JSONArray()
                .put(new JSONObject().put("key", "A").put("value", "甲"))
                .put(new JSONObject().put("key", "B").put("value", "乙")));
    }
    private static JSONArray array(String... values) {
        JSONArray result = new JSONArray();
        for (String value : values) result.put(value);
        return result;
    }
    private static ReviewRegistry.Review create(ReviewRegistry.Completion done) throws Exception {
        return ReviewRegistry.create("课程", "题目", 1, choiceProblem(), array("A"), Long.MAX_VALUE, -1, done);
    }
    @Test public void competingSubmitAndCancelFinishExactlyOnce() throws Exception {
        AtomicInteger callbacks = new AtomicInteger();
        AtomicInteger winners = new AtomicInteger();
        ReviewRegistry.Review r = create((review, cancelled) -> callbacks.incrementAndGet());
        int count=32;
        ExecutorService pool = Executors.newFixedThreadPool(count);
        CountDownLatch ready = new CountDownLatch(count), start = new CountDownLatch(1);
        List<Future<?>> futures = new ArrayList<>();
        try {
            for (int i=0; i<count; i++) {
                final boolean cancel = i%2 == 0;
                futures.add(pool.submit(() -> {
                    ready.countDown(); start.await();
                    if (cancel ? ReviewRegistry.cancel(r.id) : ReviewRegistry.submit(r.id)) winners.incrementAndGet();
                    return null;
                }));
            }
            assertTrue(ready.await(5, TimeUnit.SECONDS)); start.countDown();
            for (Future<?> f : futures) f.get(5, TimeUnit.SECONDS);
            assertEquals(1, winners.get()); assertEquals(1, callbacks.get());
            assertNull(ReviewRegistry.get(r.id)); assertFalse(ReviewRegistry.submit(r.id));
        } finally { start.countDown(); pool.shutdownNow(); }
    }
    @Test public void selectionMustBeAmongOptionsAndSingleChoiceOnly() throws Exception {
        ReviewRegistry.Review r = create((review, cancelled) -> {});
        assertFalse(ReviewRegistry.update(r.id, array("C")));
        assertFalse(ReviewRegistry.update(r.id, array("A", "B")));
        assertFalse(ReviewRegistry.update(r.id, array("")));
        assertEquals("A", r.answers.getString(0));
        assertTrue(ReviewRegistry.update(r.id, array("B")));
        assertEquals("B", r.answers.getString(0));
    }
    @Test public void multiChoiceRejectsDuplicatesAndMissingOptions() throws Exception {
        ReviewRegistry.Review r = ReviewRegistry.create("课程", "题目", 2, choiceProblem(), array("A"), Long.MAX_VALUE, -1, (review, cancelled) -> {});
        assertFalse(ReviewRegistry.update(r.id, array("A", "a")));
        assertFalse(ReviewRegistry.update(r.id, array("A", "Z")));
        assertTrue(ReviewRegistry.update(r.id, array("A", "B")));
        try {
            ReviewRegistry.create("课程", "题目2", 1, new JSONObject(), array("A"), Long.MAX_VALUE, -1, (review, cancelled) -> {});
            fail("missing options must not be accepted");
        } catch (IllegalArgumentException expected) { }
    }
    @Test public void expiredReviewCannotBeModified() throws Exception {
        ReviewRegistry.Review r = ReviewRegistry.create("课程", "题目", 1, choiceProblem(), array("A"), 0, -1, (review, cancelled) -> {});
        assertFalse(ReviewRegistry.update(r.id, array("B")));
        assertEquals("A", r.answers.getString(0));
    }
    @Test public void blankCountIsPreserved() throws Exception {
        JSONObject problem = new JSONObject().put("blanks", array("", ""));
        ReviewRegistry.Review r = ReviewRegistry.create("课程", "题目", 4, problem, array("甲", "乙"), Long.MAX_VALUE, -1, (review, cancelled) -> {});
        assertFalse(ReviewRegistry.update(r.id, array("甲")));
        assertFalse(ReviewRegistry.update(r.id, array("甲", "")));
        assertTrue(ReviewRegistry.update(r.id, array("甲", "丙")));
    }
}
