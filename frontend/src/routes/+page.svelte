<script>
  let question = '';
  let loading = false;
  let result = null;
  let error = '';

  async function ask() {
    loading = true; error = ''; result = null;
    try {
      const r = await fetch('/api/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question })
      });
      if (!r.ok) throw new Error('後端錯誤 ' + r.status);
      result = await r.json();
    } catch (e) {
      error = e.message;
    } finally {
      loading = false;
    }
  }
</script>

<main>
  <h1>法規判決 RAG</h1>
  <textarea bind:value={question} rows="3" placeholder="輸入法律問題…"></textarea>
  <button on:click={ask} disabled={loading || !question.trim()}>
    {loading ? '檢索生成中…' : '送出'}
  </button>
  {#if error}<p class="err">{error}</p>{/if}
  {#if result}
    <h2>回答</h2>
    <p class="ans">{result.answer}</p>
    <h2>引用（top {result.hits.length}）</h2>
    <ol>
      {#each result.hits as h}
        <li>{h.payload.case_no}｜{h.payload.law}｜{h.score.toFixed(3)}<br />{h.payload.text.slice(0, 200)}…</li>
      {/each}
    </ol>
  {/if}
</main>

<style>
  main { max-width: 800px; margin: 2rem auto; padding: 0 1rem; font-family: sans-serif; }
  textarea { width: 100%; }
  .ans { white-space: pre-wrap; }
  .err { color: red; }
</style>
