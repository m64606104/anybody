// Web Worker: 后台计时器
// 即使页面进入后台也能正常运行

const timers = {};

self.onmessage = function(e) {
  const { action, chatId, delay } = e.data;
  
  if (action === 'start') {
    // 清除已有的计时器
    if (timers[chatId]) {
      clearTimeout(timers[chatId]);
    }
    // 启动新计时器
    timers[chatId] = setTimeout(() => {
      self.postMessage({ chatId, action: 'flush' });
      delete timers[chatId];
    }, delay);
  } else if (action === 'clear') {
    if (timers[chatId]) {
      clearTimeout(timers[chatId]);
      delete timers[chatId];
    }
  }
};
