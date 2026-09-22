// Pocket Signals — интерфейс (vanilla JS, без фреймворков)
document.addEventListener('DOMContentLoaded', function () {
  // Автоскрытие уведомлений
  document.querySelectorAll('.flash').forEach(function (el) {
    setTimeout(function () {
      el.classList.add('flash-hide');
      setTimeout(function () { el.remove(); }, 400);
    }, 6500);
  });

  // Лоадер на кнопке «Получить сигнал» (ИИ отвечает 10-60+ секунд)
  var form = document.querySelector('.signal-form');
  var btn = document.getElementById('signal-btn');
  if (form && btn) {
    form.addEventListener('submit', function () {
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner"></span> ИИ анализирует рынок (до 1–2 мин)…';
    });
  }

  // Админка: смена тарифа — автоотправка формы
  document.querySelectorAll('select[data-autosubmit]').forEach(function (sel) {
    sel.addEventListener('change', function () { sel.form.submit(); });
  });
});
