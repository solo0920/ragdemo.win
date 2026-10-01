<!-- 全域 design token（見 src/lib/theme.css 的檔頭說明）。 -->
<script>
  import '$lib/theme.css';
  let { children } = $props();
</script>

{@render children()}
