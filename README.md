# Exam Hall

SQLite və Python standart kitabxanası üzərində qurulmuş imtahan otaqlarının idarəetmə tətbiqi.

## İşə salmaq

Python 3.10+ tələb olunur. Layihə qovluğunda:

```powershell
$env:EXAM_ADMIN_PASSWORD = 'burada-guclu-ilkin-sifre'
$env:EXAM_HOST = '127.0.0.1'
python server.py
```

Sonra `http://localhost:8000` ünvanını açın. İlk başladılmada `examrooms.sqlite3` yaranır. `EXAM_ADMIN_PASSWORD` təyin edilməsə, ilk Admin hesabı `admin / admin123` olur. İlk girişdən sonra Users bölməsində yeni hesab yaradın və ilkin şifrəni dəyişməzdən əvvəl mühit dəyişənindən istifadə etməyi tövsiyə edirik.

Telefon və ya eyni LAN-dakı başqa cihazlardan giriş üçün `EXAM_HOST` dəyərini kompüterin lokal IPv4 ünvanına (məsələn, `192.168.1.20`) dəyişin. Sonra telefonda `http://<kompüter-ip>:8000` açın. Eyni Wi-Fi şəbəkəsində olmalıdır; Windows Firewall bağlantını bloklaya bilər.

## İmkanlar

- Admin və Creator girişləri; User rolu saxlanılır, lakin daxil ola bilmir.
- PBKDF2-HMAC-SHA256 şifrə heşləri, HttpOnly sessiya cookie-si və server tərəfində RBAC.
- Public/Private otaqlar, unikal dörd rəqəmli ID, iki oturacaqlı parta düzülüşü.
- Oturacaq taymerinin başlanğıcı server bazasında saxlanır; 30 saniyəlik əlavə müddət və əl ilə boşaltma.
- Admin/Creator panelləri hər 2.5 saniyədə serverdən sinxronlaşır.
- Otaq və istifadəçi axtarışı, status/rol filtrləri, light/dark tema.

## İstehsal mühiti

Bu başlanğıc tətbiq SQLite və quraşdırmasız Python serverindən istifadə edir. Bir server prosesi üçün işləkdir. İctimai şəbəkədə yerləşdirməzdən əvvəl HTTPS reverse proxy, CSRF qoruması, rate limiting və çoxprosesli/çoxinstanslı real-time transport (məsələn, WebSocket + paylaşılmış DB) əlavə edin. Hazırkı sinxronizasiya qısa intervalı olan server polling-dir.
