from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse
import hashlib, hmac, json, os, secrets, sqlite3, time, threading

ROOT = Path(__file__).parent
DB = ROOT / 'examrooms.sqlite3'
HOST = os.environ.get('EXAM_HOST', '127.0.0.1')
COOKIE = 'exam_session'
LOCK = threading.RLock()

def connect():
    c = sqlite3.connect(DB, timeout=10); c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON'); return c

def pw_hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    return salt.hex() + ':' + hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 240000).hex()

def pw_ok(password, value):
    try:
        salt, digest = value.split(':'); return hmac.compare_digest(hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 240000).hex(), digest)
    except Exception: return False

def init():
    with connect() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, login TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('Admin','Creator','User')), status TEXT NOT NULL DEFAULT 'Active', created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, expires_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS lobbies(id INTEGER PRIMARY KEY, lobby_id TEXT UNIQUE NOT NULL, name TEXT NOT NULL, type TEXT NOT NULL, password_hash TEXT, rows INTEGER NOT NULL, columns INTEGER NOT NULL, timer_duration INTEGER NOT NULL, creator_id INTEGER NOT NULL REFERENCES users(id), status TEXT NOT NULL DEFAULT 'Active', created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS seats(id INTEGER PRIMARY KEY, lobby_pk INTEGER NOT NULL REFERENCES lobbies(id) ON DELETE CASCADE, row_number INTEGER NOT NULL, column_number INTEGER NOT NULL, position INTEGER NOT NULL, started_at INTEGER, duration_seconds INTEGER, status TEXT NOT NULL DEFAULT 'Available', UNIQUE(lobby_pk,row_number,column_number,position));
        CREATE TABLE IF NOT EXISTS seat_undos(token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, lobby_pk INTEGER NOT NULL REFERENCES lobbies(id) ON DELETE CASCADE, seat_id INTEGER NOT NULL REFERENCES seats(id) ON DELETE CASCADE, started_at INTEGER NOT NULL, duration_seconds INTEGER NOT NULL, cleared_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, used_at INTEGER);''')
        if not c.execute('SELECT 1 FROM users WHERE role="Admin"').fetchone():
            c.execute('INSERT INTO users(login,password_hash,role,created_at) VALUES(?,?,?,?)',('admin',pw_hash(os.environ.get('EXAM_ADMIN_PASSWORD','admin123')), 'Admin',int(time.time()*1000)))

def safe_user(r): return {'id':r['id'],'login':r['login'],'role':r['role'],'status':r['status'],'createdAt':r['created_at']}
class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*a,**kw): super().__init__(*a,directory=str(ROOT),**kw)
    def log_message(self,*a): pass
    def send_json(self, data, status=200):
        raw=json.dumps(data,ensure_ascii=False).encode(); self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(raw))); self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(raw)
    def body(self):
        try: return json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))) or b'{}')
        except Exception: raise ValueError('Yanlış JSON məlumatı')
    def auth(self):
        token=''
        for item in self.headers.get('Cookie','').split(';'):
            if item.strip().startswith(COOKIE+'='): token=item.strip().split('=',1)[1]
        with connect() as c:
            r=c.execute('SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires_at>?',(token,int(time.time()*1000))).fetchone()
            return safe_user(r) if r and r['status']=='Active' else None
    def do_GET(self):
        path=urlparse(self.path).path
        if path.startswith('/api/'):
            u=self.auth()
            if path!='/api/me' and not u: return self.send_json({'error':'Giriş tələb olunur'},401)
            try:
                with connect() as c:
                    if path=='/api/me': return self.send_json({'user':u})
                    if path=='/api/data':
                        users=[safe_user(x) for x in c.execute('SELECT * FROM users ORDER BY created_at DESC') ] if u['role']=='Admin' else []
                        rows=c.execute('SELECT l.*,u.login creator FROM lobbies l JOIN users u ON u.id=l.creator_id ORDER BY l.created_at DESC').fetchall()
                        lobbies=[]
                        for r in rows:
                            if u['role']=='Creator' and r['creator_id']!=u['id']: pass
                            lobbies.append({'id':r['id'],'lobbyId':r['lobby_id'],'name':r['name'],'type':r['type'],'rows':r['rows'],'columns':r['columns'],'timerDuration':r['timer_duration'],'creatorId':r['creator_id'],'creator':r['creator'],'status':r['status'],'createdAt':r['created_at'],'seats':[dict(x) for x in c.execute('SELECT id,row_number row,column_number col,position,status,started_at startedAt,duration_seconds duration FROM seats WHERE lobby_pk=? ORDER BY row_number,column_number,position',(r['id'],))]})
                        return self.send_json({'user':u,'users':users,'lobbies':lobbies,'serverTime':int(time.time()*1000)})
                    return self.send_json({'error':'Tapılmadı'},404)
            except Exception as e: return self.send_json({'error':str(e)},400)
        return super().do_GET()
    def do_POST(self):
        path=urlparse(self.path).path
        try: d=self.body()
        except ValueError as e: return self.send_json({'error':str(e)},400)
        try:
            with connect() as c:
                if path=='/api/login':
                    r=c.execute('SELECT * FROM users WHERE login=?',(str(d.get('login','')).strip(),)).fetchone()
                    if not r or r['status']!='Active' or r['role']=='User' or not pw_ok(str(d.get('password','')),r['password_hash']): return self.send_json({'error':'Login və ya şifrə yanlışdır'},401)
                    token=secrets.token_urlsafe(32); c.execute('INSERT INTO sessions VALUES(?,?,?)',(token,r['id'],int(time.time()*1000)+86400000)); self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Set-Cookie',f'{COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=86400'); self.end_headers(); self.wfile.write(json.dumps({'user':safe_user(r)}).encode()); return
                u=self.auth()
                if path=='/api/logout':
                    if u:
                        token=next((p.strip().split('=',1)[1] for p in self.headers.get('Cookie','').split(';') if p.strip().startswith(COOKIE+'=')), '')
                        c.execute('DELETE FROM sessions WHERE token=?',(token,))
                    self.send_response(200); self.send_header('Set-Cookie',f'{COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'); self.end_headers(); return
                if not u: return self.send_json({'error':'Giriş tələb olunur'},401)
                if path=='/api/users':
                    if u['role']!='Admin': return self.send_json({'error':'İcazə yoxdur'},403)
                    login=str(d.get('login','')).strip(); password=str(d.get('password','')); role=d.get('role')
                    if len(login)<3 or len(password)<8 or role not in ('Admin','Creator','User'): return self.send_json({'error':'Login ən azı 3, şifrə ən azı 8 simvol olmalı və rol seçilməlidir'},400)
                    c.execute('INSERT INTO users(login,password_hash,role,created_at) VALUES(?,?,?,?)',(login,pw_hash(password),role,int(time.time()*1000))); return self.send_json({'ok':True},201)
                if path.startswith('/api/users/') and path.endswith('/password'):
                    if u['role']!='Admin': return self.send_json({'error':'İcazə yoxdur'},403)
                    uid=int(path.split('/')[-2]); password=str(d.get('password',''))
                    if len(password)<8:return self.send_json({'error':'Yeni şifrə ən azı 8 simvol olmalıdır'},400)
                    if not c.execute('SELECT 1 FROM users WHERE id=?',(uid,)).fetchone():return self.send_json({'error':'İstifadəçi tapılmadı'},404)
                    c.execute('UPDATE users SET password_hash=? WHERE id=?',(pw_hash(password),uid));return self.send_json({'ok':True})
                if path=='/api/lobbies':
                    if u['role'] not in ('Admin','Creator'): return self.send_json({'error':'User rolu aktiv deyil'},403)
                    name=str(d.get('name','')).strip(); typ=d.get('type'); nr=int(d.get('rows',0)); nc=int(d.get('columns',0)); mins=int(d.get('timerDuration',0)); secret=d.get('password','')
                    if not name or typ not in ('Public','Private') or not 1<=nr<=10 or not 1<=nc<=10 or not 1<=mins<=600 or (typ=='Private' and len(secret)<4): return self.send_json({'error':'Otaq məlumatlarını yoxlayın'},400)
                    lid=None
                    for _ in range(100):
                        candidate=f'{secrets.randbelow(10000):04d}'
                        if not c.execute('SELECT 1 FROM lobbies WHERE lobby_id=?',(candidate,)).fetchone(): lid=candidate; break
                    if lid is None: return self.send_json({'error':'Unikal ID yaradıla bilmədi'},503)
                    cur=c.execute('INSERT INTO lobbies(lobby_id,name,type,password_hash,rows,columns,timer_duration,creator_id,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(lid,name,typ,pw_hash(secret) if typ=='Private' else None,nr,nc,mins*60,u['id'],int(time.time()*1000))); pk=cur.lastrowid
                    c.executemany('INSERT INTO seats(lobby_pk,row_number,column_number,position) VALUES(?,?,?,?)',[(pk,r,col,pos) for r in range(1,nr+1) for col in range(1,nc+1) for pos in (1,2)])
                    return self.send_json({'lobbyId':lid},201)
                if path.startswith('/api/lobbies/'):
                    parts=path.split('/'); lid=parts[3]; l=c.execute('SELECT * FROM lobbies WHERE lobby_id=?',(lid,)).fetchone()
                    if not l:return self.send_json({'error':'Otaq tapılmadı'},404)
                    if path.endswith('/enter') and l['type']=='Private' and not pw_ok(str(d.get('password','')),l['password_hash']): return self.send_json({'error':'Otaq parolu yanlışdır'},403)
                    if len(parts)==5 and parts[4]=='seat':
                        if u['role']!='Admin' and l['creator_id']!=u['id']:return self.send_json({'error':'Bu otağın oturacaqlarını idarə etmək icazəniz yoxdur'},403)
                        seat=c.execute('SELECT * FROM seats WHERE id=? AND lobby_pk=?',(int(d['seatId']),l['id'])).fetchone()
                        if not seat:return self.send_json({'error':'Oturacaq tapılmadı'},404)
                        if u['role']=='User':return self.send_json({'error':'User rolu aktiv deyil'},403)
                        if parts[4]=='seat':
                            action=d.get('action')
                            if action=='start' and seat['status']=='Available': c.execute('UPDATE seats SET status="Active",started_at=?,duration_seconds=? WHERE id=?',(int(time.time()*1000),l['timer_duration'],seat['id']))
                            elif action=='clear' and seat['status'] in ('Active','Expired'):
                                now=int(time.time()*1000); token=secrets.token_urlsafe(24)
                                c.execute('INSERT INTO seat_undos(token,user_id,lobby_pk,seat_id,started_at,duration_seconds,cleared_at,expires_at) VALUES(?,?,?,?,?,?,?,?)',(token,u['id'],l['id'],seat['id'],seat['started_at'],seat['duration_seconds'],now,now+30000))
                                c.execute('UPDATE seats SET status="Available",started_at=NULL,duration_seconds=NULL WHERE id=?',(seat['id'],))
                                return self.send_json({'ok':True,'undoToken':token,'undoExpiresAt':now+30000})
                            else:return self.send_json({'error':'Əməliyyat mümkün deyil'},409)
                            return self.send_json({'ok':True})
                    if len(parts)==6 and parts[4:] == ['seat','undo']:
                        if u['role']!='Admin' and l['creator_id']!=u['id']:return self.send_json({'error':'İcazə yoxdur'},403)
                        token=str(d.get('token','')); now=int(time.time()*1000)
                        undo=c.execute('SELECT * FROM seat_undos WHERE token=? AND lobby_pk=? AND user_id=? AND used_at IS NULL AND expires_at>?',(token,l['id'],u['id'],now)).fetchone()
                        if not undo:return self.send_json({'error':'Geri qaytarma müddəti bitib'},410)
                        seat=c.execute('SELECT * FROM seats WHERE id=? AND lobby_pk=?',(undo['seat_id'],l['id'])).fetchone()
                        if not seat or seat['status']!='Available':return self.send_json({'error':'Oturacaq artıq dəyişdirilib'},409)
                        c.execute('UPDATE seats SET status="Active",started_at=?,duration_seconds=? WHERE id=?',(undo['started_at']+(now-undo['cleared_at']),undo['duration_seconds'],seat['id']))
                        c.execute('UPDATE seat_undos SET used_at=? WHERE token=?',(now,token));return self.send_json({'ok':True})
                    if len(parts)==5 and parts[4]=='seat-swap':
                        if u['role']!='Admin' and l['creator_id']!=u['id']:return self.send_json({'error':'İcazə yoxdur'},403)
                        row=int(d.get('row',0)); col=int(d.get('column',0))
                        pair=c.execute('SELECT * FROM seats WHERE lobby_pk=? AND row_number=? AND column_number=? ORDER BY position',(l['id'],row,col)).fetchall()
                        if len(pair)!=2:return self.send_json({'error':'Parta tapılmadı'},404)
                        a,b=pair
                        c.execute('UPDATE seats SET status=?,started_at=?,duration_seconds=? WHERE id=?',(b['status'],b['started_at'],b['duration_seconds'],a['id']))
                        c.execute('UPDATE seats SET status=?,started_at=?,duration_seconds=? WHERE id=?',(a['status'],a['started_at'],a['duration_seconds'],b['id']))
                        return self.send_json({'ok':True})
                    if path.endswith('/status'):
                        if u['role']!='Admin' and l['creator_id']!=u['id']:return self.send_json({'error':'İcazə yoxdur'},403)
                        c.execute('UPDATE lobbies SET status=? WHERE id=?',('Inactive' if l['status']=='Active' else 'Active',l['id']));return self.send_json({'ok':True})
                    if path.endswith('/delete'):
                        if u['role']!='Admin' and l['creator_id']!=u['id']:return self.send_json({'error':'İcazə yoxdur'},403)
                        c.execute('DELETE FROM lobbies WHERE id=?',(l['id'],)); return self.send_json({'ok':True})
                    if path.endswith('/enter'): return self.send_json({'ok':True})
                return self.send_json({'error':'Tapılmadı'},404)
        except sqlite3.IntegrityError: return self.send_json({'error':'Bu login artıq mövcuddur'},409)
        except Exception as e: return self.send_json({'error':str(e)},400)
    def do_PATCH(self):
        path=urlparse(self.path).path
        try:d=self.body();u=self.auth()
        except Exception:return self.send_json({'error':'Sorğu yanlışdır'},400)
        if not u:return self.send_json({'error':'Giriş tələb olunur'},401)
        try:
            with connect() as c:
                if path.startswith('/api/lobbies/'):
                    lid=path.split('/')[-1]; lobby=c.execute('SELECT * FROM lobbies WHERE lobby_id=?',(lid,)).fetchone()
                    if not lobby:return self.send_json({'error':'Otaq tapılmadı'},404)
                    if u['role']!='Admin' and (u['role']!='Creator' or lobby['creator_id']!=u['id']):return self.send_json({'error':'İcazə yoxdur'},403)
                    name=str(d.get('name',lobby['name'])).strip(); typ=d.get('type',lobby['type']); nr=int(d.get('rows',lobby['rows'])); nc=int(d.get('columns',lobby['columns'])); mins=int(d.get('timerDuration',lobby['timer_duration']//60)); secret=str(d.get('password',''))
                    if not name or typ not in ('Public','Private') or not 1<=nr<=10 or not 1<=nc<=10 or not 1<=mins<=600:return self.send_json({'error':'Otaq məlumatlarını yoxlayın'},400)
                    changing_layout=(nr!=lobby['rows'] or nc!=lobby['columns'])
                    if changing_layout and c.execute('SELECT 1 FROM seats WHERE lobby_pk=? AND status<>"Available" LIMIT 1',(lobby['id'],)).fetchone():return self.send_json({'error':'Oturacaqlar dolu ikən sıra və sütun sayı dəyişdirilə bilməz'},409)
                    passhash=pw_hash(secret) if typ=='Private' and secret else lobby['password_hash'] if typ=='Private' else None
                    if typ=='Private' and not passhash:return self.send_json({'error':'Private otaq üçün parol təyin edin'},400)
                    c.execute('UPDATE lobbies SET name=?,type=?,password_hash=?,rows=?,columns=?,timer_duration=? WHERE id=?',(name,typ,passhash,nr,nc,mins*60,lobby['id']))
                    if changing_layout:
                        c.execute('DELETE FROM seats WHERE lobby_pk=?',(lobby['id'],));c.executemany('INSERT INTO seats(lobby_pk,row_number,column_number,position) VALUES(?,?,?,?)',[(lobby['id'],r,col,pos) for r in range(1,nr+1) for col in range(1,nc+1) for pos in (1,2)])
                    return self.send_json({'ok':True})
                if u['role']!='Admin':return self.send_json({'error':'İcazə yoxdur'},403)
                uid=int(path.rsplit('/',1)[1]); target=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
                if not target:return self.send_json({'error':'İstifadəçi tapılmadı'},404)
                if target['id']==u['id'] and (d.get('status','Active')!='Active' or d.get('role',target['role'])!='Admin'):return self.send_json({'error':'Öz admin hesabınızı deaktiv və ya rolunu dəyişə bilməzsiniz'},400)
                c.execute('UPDATE users SET role=?,status=? WHERE id=?',(d.get('role',target['role']),d.get('status',target['status']),uid));return self.send_json({'ok':True})
        except Exception as e:return self.send_json({'error':str(e)},400)

if __name__=='__main__':
    init(); print(f'Exam rooms: http://{HOST}:8000 (admin / admin123)'); ThreadingHTTPServer((HOST,8000),Handler).serve_forever()
