(() => {
  const canvas = document.querySelector('#cloudEffect canvas');
  const host = document.getElementById('cloudEffect');
  const ctx = canvas?.getContext('2d');
  if (!canvas || !host || !ctx) return;

  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const clouds = [];
  const random = (min, max) => min + Math.random() * (max - min);
  let width = 1;
  let height = 1;
  let ratio = 1;
  let lastTime = 0;
  let frameId = 0;
  let visible = true;

  function spawn(cloud, fromTop = false) {
    cloud.width = random(90, 165);
    cloud.height = cloud.width * random(0.40, 0.53);
    cloud.x = random(-cloud.width * 0.4, width - cloud.width * 0.6);
    cloud.y = fromTop ? -cloud.height - random(10, 140) : random(-cloud.height * 0.25, height);
    cloud.fall = random(9, 20);
    cloud.drift = random(-5, 7);
    cloud.sway = random(4, 13);
    cloud.phase = random(0, Math.PI * 2);
    cloud.rate = random(0.16, 0.34);
    cloud.opacity = random(0.42, 0.62);
    cloud.depth = random(0.78, 1.12);
  }

  function pathForCloud(w, h) {
    ctx.beginPath();
    ctx.moveTo(-0.48 * w, 0.18 * h);
    ctx.bezierCurveTo(-0.61 * w, 0.16 * h, -0.59 * w, -0.04 * h, -0.43 * w, -0.06 * h);
    ctx.bezierCurveTo(-0.40 * w, -0.29 * h, -0.13 * w, -0.35 * h, 0.00, -0.20 * h);
    ctx.bezierCurveTo(0.10 * w, -0.46 * h, 0.43 * w, -0.37 * h, 0.45 * w, -0.09 * h);
    ctx.bezierCurveTo(0.65 * w, -0.08 * h, 0.64 * w, 0.19 * h, 0.43 * w, 0.20 * h);
    ctx.lineTo(-0.39 * w, 0.20 * h);
    ctx.bezierCurveTo(-0.43 * w, 0.20 * h, -0.46 * w, 0.19 * h, -0.48 * w, 0.18 * h);
    ctx.closePath();
  }

  function drawCloud(cloud, time) {
    const w = cloud.width;
    const h = cloud.height;
    const sway = Math.sin(time * cloud.rate + cloud.phase) * cloud.sway;
    const x = cloud.x + sway;
    const y = cloud.y;
    ctx.save();
    ctx.translate(x, y);
    ctx.globalAlpha = cloud.opacity;

    pathForCloud(w, h);
    ctx.save();
    ctx.translate(0, h * 0.055);
    ctx.shadowColor = 'rgba(73, 125, 180, 0.42)';
    ctx.shadowBlur = h * 0.22;
    ctx.fillStyle = 'rgba(119, 163, 207, 0.56)';
    ctx.fill();
    ctx.restore();

    pathForCloud(w, h);
    const body = ctx.createLinearGradient(0, -h * 0.35, 0, h * 0.22);
    body.addColorStop(0, 'rgba(255, 255, 255, 0.99)');
    body.addColorStop(0.48, 'rgba(237, 247, 255, 0.98)');
    body.addColorStop(1, 'rgba(169, 204, 235, 0.94)');
    ctx.fillStyle = body;
    ctx.fill();
    ctx.lineWidth = Math.max(1, h * 0.018);
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.82)';
    ctx.stroke();

    const highlight = ctx.createRadialGradient(-w * 0.13, -h * 0.18, 1, -w * 0.13, -h * 0.18, w * 0.42);
    highlight.addColorStop(0, 'rgba(255, 255, 255, 0.58)');
    highlight.addColorStop(1, 'rgba(255, 255, 255, 0)');
    pathForCloud(w, h);
    ctx.fillStyle = highlight;
    ctx.fill();
    ctx.restore();
  }

  function resize() {
    const bounds = host.getBoundingClientRect();
    width = Math.max(1, bounds.width);
    height = Math.max(1, bounds.height);
    ratio = Math.min(window.devicePixelRatio || 1, 1.5);
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    const target = Math.max(8, Math.min(24, Math.round(width * height / 105000)));
    while (clouds.length < target) {
      const cloud = {};
      spawn(cloud);
      clouds.push(cloud);
    }
    clouds.length = target;
    draw(0);
    wake();
  }

  function draw(time) {
    ctx.clearRect(0, 0, width, height);
    for (const cloud of clouds) drawCloud(cloud, time);
  }

  function tick(timestamp) {
    frameId = 0;
    if (!visible || document.hidden) return;
    const elapsed = lastTime ? Math.min(0.05, (timestamp - lastTime) / 1000) : 1 / 60;
    lastTime = timestamp;
    if (!reducedMotion) {
      const seconds = timestamp / 1000;
      for (const cloud of clouds) {
        cloud.y += cloud.fall * cloud.depth * elapsed;
        cloud.x += cloud.drift * cloud.depth * elapsed;
        if (cloud.y > height + cloud.height) spawn(cloud, true);
        if (cloud.x > width + cloud.width) cloud.x = -cloud.width;
        if (cloud.x < -cloud.width * 1.2) cloud.x = width + cloud.width * 0.2;
      }
      draw(seconds);
      frameId = requestAnimationFrame(tick);
    } else {
      draw(0);
    }
  }

  function wake() {
    if (!frameId && visible && !document.hidden && !reducedMotion) frameId = requestAnimationFrame(tick);
  }

  const resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(host);
  document.addEventListener('visibilitychange', () => {
    visible = !document.hidden;
    lastTime = 0;
    wake();
  });
  resize();
})();
