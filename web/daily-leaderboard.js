// Shared by the Daily panel and the main leaderboard dialog.
class DailyLeaderboard {
  constructor({body, day, prefix, active = () => true}) {
    this.body = document.querySelector(body);
    this.day = document.querySelector(day);
    this.info = document.querySelector(`#${prefix}Info`);
    this.me = document.querySelector(`#${prefix}Me`);
    this.locate = document.querySelector(`#${prefix}Locate`);
    this.prev = document.querySelector(`#${prefix}Prev`);
    this.next = document.querySelector(`#${prefix}Next`);
    this.active = active;
    this.offset = 0;
    this.limit = 10;
    this.version = 0;
    this.prev.addEventListener('click', () => { this.offset = Math.max(0, this.offset-this.limit); this.refresh(); });
    this.next.addEventListener('click', () => { this.offset += this.limit; this.refresh(); });
    this.locate.addEventListener('click', () => this.refresh({locate: true}));
    this.day.addEventListener('change', () => this.refresh({reset: true}));
  }
  async refresh({reset = false, locate = false} = {}) {
    if (!this.active()) return;
    const day = this.day.value, session = token(), version = ++this.version;
    if (reset || day !== this.lastDay || session !== this.lastSession) {
      this.offset = 0;
      this.me.textContent = '';
      this.locate.classList.add('hidden');
      this.body.innerHTML = '<tr><td colspan="5">Đang tải…</td></tr>';
    }
    this.lastDay = day; this.lastSession = session;
    this.prev.disabled = this.next.disabled = this.locate.disabled = true;
    this.info.textContent = 'Đang tải bảng xếp hạng…';
    const query = new URLSearchParams({offset: this.offset, limit: this.limit});
    if (day) query.set('day', day);
    if (locate) query.set('locate_me', 'true');
    try {
      const page = await request(`/api/daily/leaderboard?${query}`);
      if (version !== this.version || token() !== session || !this.active()) return;
      this.offset = page.offset;
      this.body.innerHTML = page.items.length ? page.items.map(r => {
        const mine = r.player_id === page.me?.player_id;
        return `<tr${mine ? ' class="daily-standing-own"' : ''}><td><span class="rank-badge${r.rank <= 3 ? ' top' : ''}">${r.rank}</span></td><td><strong>${escapeHtml(r.name)}</strong>${mine ? '<small class="daily-standing-you">Đội của bạn</small>' : ''}</td><td><strong>${Number(r.elo).toLocaleString('vi-VN', {maximumFractionDigits: 1})}</strong></td><td>${Number(r.points).toLocaleString('vi-VN')}</td><td>${r.matches}</td></tr>`;
      }).join('') : '<tr><td colspan="5">Chưa có kết quả Daily trong phạm vi đã chọn.</td></tr>';
      this.info.textContent = page.total ? `Hiển thị ${page.offset+1}–${page.offset+page.items.length} trên ${page.total} đội · Trang ${Math.floor(page.offset/page.limit)+1}/${Math.ceil(page.total/page.limit)}` : 'Hiển thị 0 trên 0 đội';
      this.prev.disabled = !page.has_previous; this.next.disabled = !page.has_next;
      this.locate.classList.toggle('hidden', !page.me);
      this.locate.disabled = !page.me;
      this.me.textContent = page.me ? `Đội bạn: hạng ${page.me.rank} · Elo ${Number(page.me.elo).toLocaleString('vi-VN', {maximumFractionDigits: 1})} · ${page.me.matches} trận` : page.signed_in ? 'Đội bạn chưa có kết quả Daily trong phạm vi đã chọn.' : 'Đăng nhập để xem thứ hạng của đội bạn.';
    } catch (error) {
      if (version !== this.version || token() !== session || !this.active()) return;
      this.body.innerHTML = '<tr><td colspan="5">Chưa tải được bảng xếp hạng. Hãy thử lại.</td></tr>';
      this.info.textContent = error.message;
      this.me.textContent = ''; this.locate.classList.add('hidden');
    }
  }
}
