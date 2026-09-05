document.addEventListener('DOMContentLoaded', function () {
  var inner = document.querySelector('.md-header__inner');
  if (!inner) return;
  var div = document.createElement('div');
  div.className = 'itheima-header-logo';
  var depth = window.location.pathname.split('/').filter(Boolean).length;
  var prefix = depth <= 1 ? '.' : '..';
  div.innerHTML = '<img src="' + prefix + '/img/logo1.png" alt="黑马程序员">';
  // 插入到搜索按钮之前，配合 margin-left: auto 推到搜索框旁边
  var searchLabel = document.querySelector('.md-header__button[for="__search"]');
  if (searchLabel) {
    inner.insertBefore(div, searchLabel);
  } else {
    inner.appendChild(div);
  }
});
