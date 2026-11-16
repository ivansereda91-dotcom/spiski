# Списки

Телеграм-бот для двоих: общие списки **покупок** и **дел**. Добавить можно кнопкой или просто текстом,
сразу несколько пунктов. В списке нажимаешь на пункт — ставится галочка. Отмеченные убираются одной кнопкой.
Когда один добавляет, второму приходит уведомление (выключается командой /notify).

```
git clone https://github.com/ivansereda91-dotcom/spiski /opt/spiski
bash /opt/spiski/setup.sh
```

Доступ: `bash /opt/spiski/allow.sh ID`. Обновление: `cd /opt/spiski && git pull && systemctl restart spiski`.
Данные: `/var/lib/spiski/spiski.db`.
